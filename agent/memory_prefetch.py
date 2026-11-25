"""AgentCore Memory session manager that starts the long-term memory lookup early.

`AgentCoreMemorySessionManager.retrieve_customer_context` runs when the user's
message is added to the conversation, which is after the agent has been built and
after the message was stored, and the model call waits for it. The lookup needs
only the question and the memory configuration, so this subclass lets the caller
start it at the beginning of the request (`start_prefetch`) and use the result at
the same point where the SDK would run the lookup. The namespaces, the `top_k`, the
score filter and the `<user_context>` block inserted at the front of the user's
message are the SDK's.

One thing differs from the SDK: a remembered record that names a language is left
out (see `names_a_language`). The memory strategies save "reply in Japanese" as a
preference after a chat in Japanese, and a later chat in another app language then
follows the old record instead of the app language. The app language is the only
language signal, so the records never reach the model.

If no prefetch was started, if the question differs from the prefetched one, or if
the prefetch failed, the lookup runs when the message is added, with the same filter.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from note_guard import strip_reply_language_line

logger = logging.getLogger(__name__)

# How the app's ten languages are named, in English and in the languages themselves. A record that holds one
# of these is about a language ("reply language is Japanese", "prefers Korean"). Names written with Latin
# letters match as whole words; names in other scripts match anywhere in the text.
_LATIN_LANGUAGE_NAMES = (
    "english", "japanese", "korean", "spanish", "french", "german", "chinese", "mandarin", "malay", "thai",
    "indonesian", "inglés", "japonés", "coreano", "español", "francés", "alemán", "chino", "malayo",
    "tailandés", "indonesio", "anglais", "japonais", "coréen", "espagnol", "français", "allemand", "chinois",
    "malais", "thaï", "indonésien", "englisch", "japanisch", "koreanisch", "spanisch", "französisch",
    "deutsch", "chinesisch", "malaiisch", "thailändisch", "indonesisch", "bahasa melayu",
    "bahasa indonesia", "bahasa inggeris", "bahasa inggris", "bahasa jepun", "bahasa jepang", "bahasa korea",
    "bahasa thai", "bahasa cina", "bahasa mandarin", "bahasa tionghoa", "bahasa sepanyol", "bahasa spanyol",
    "bahasa perancis", "bahasa prancis", "bahasa jerman",
)
_OTHER_SCRIPT_LANGUAGE_NAMES = (
    "日本語", "日本语", "日语", "日語", "日文", "일본어", "韓国語", "한국어", "韩语", "韩文", "韓語", "英語", "英语",
    "英文", "영어", "中国語", "中文", "중국어", "简体中文", "スペイン語", "西班牙语", "스페인어", "フランス語",
    "法语", "프랑스어", "ドイツ語", "德语", "독일어", "マレー語", "马来语", "말레이어", "インドネシア語", "印尼语",
    "印度尼西亚语", "인도네시아어", "タイ語", "泰语", "태국어", "ภาษาไทย", "ภาษาอังกฤษ", "ภาษาญี่ปุ่น",
    "ภาษาเกาหลี", "ภาษาจีน", "ภาษาสเปน", "ภาษาฝรั่งเศส", "ภาษาเยอรมัน", "ภาษามลายู", "ภาษาอินโดนีเซีย",
    "返信言語", "言語設定",
)
_LANGUAGE_NAME = re.compile(
    r"(?<![^\W\d_])(?:" + "|".join(re.escape(n) for n in _LATIN_LANGUAGE_NAMES) + r")(?![^\W\d_])"
    + "|" + "|".join(re.escape(n) for n in _OTHER_SCRIPT_LANGUAGE_NAMES),
    re.IGNORECASE,
)


def names_a_language(text: str) -> bool:
    """True if `text` holds the name of one of the app's languages, in English or in that language."""
    return bool(_LANGUAGE_NAME.search(text))


class PrefetchingSessionManager(AgentCoreMemorySessionManager):
    """Same as the SDK session manager, plus `start_prefetch(query)` and the language filter."""

    _prefetch: Optional[tuple[str, list]] = None

    def _lookup(self, namespace: str, retrieval_config: Any, query: str) -> list[str]:
        """One namespace, the way the SDK does it. Returns the matching texts that name no language."""
        resolved = namespace.format(
            actorId=self.config.actor_id,
            sessionId=self.config.session_id,
            memoryStrategyId=retrieval_config.strategy_id or "",
        )
        memories = self.memory_client.retrieve_memories(
            memory_id=self.config.memory_id,
            namespace_path=resolved,
            query=query,
            top_k=retrieval_config.top_k,
        )
        if retrieval_config.relevance_score:
            memories = [m for m in memories if m.get("score", 0.0) >= retrieval_config.relevance_score]
        texts = []
        dropped = 0
        for memory in memories:
            if isinstance(memory, dict):
                content = memory.get("content", {})
                if isinstance(content, dict):
                    text = content.get("text", "").strip()
                    if text and names_a_language(text):
                        dropped += 1
                    elif text:
                        texts.append(text)
        if dropped:
            logger.info("Left out %s remembered records that name a language", dropped)
        return texts

    def start_prefetch(self, query: str) -> None:
        """Begin the lookups for `query` in the background and return at once."""
        config = self.config.retrieval_config
        if not config or not query:
            return
        pool = ThreadPoolExecutor(max_workers=len(config), thread_name_prefix="memory-prefetch")
        futures = [(ns, pool.submit(self._lookup, ns, rc, query)) for ns, rc in config.items()]
        pool.shutdown(wait=False)
        self._prefetch = (query, futures)

    def retrieve_customer_context(self, event) -> None:
        pending, self._prefetch = self._prefetch, None  # a prefetch is used once
        messages = event.agent.messages
        if not messages or messages[-1].get("role") != "user" or not self.config.retrieval_config:
            return
        content = messages[-1].get("content")
        if not content or "text" not in content[0]:
            return
        # The model's copy of the message may end with the app-language reminder line; the question is the same.
        question = strip_reply_language_line(content[0]["text"])
        if pending is not None and pending[0] == question:
            futures = pending[1]
        else:
            pool = ThreadPoolExecutor(
                max_workers=len(self.config.retrieval_config), thread_name_prefix="memory-lookup"
            )
            futures = [(ns, pool.submit(self._lookup, ns, rc, question)) for ns, rc in self.config.retrieval_config.items()]
            pool.shutdown(wait=False)

        all_context: list[str] = []
        for namespace, future in futures:
            try:
                all_context.extend(future.result())
            except Exception as e:  # noqa: BLE001 - one namespace failing must not stop the others
                logger.error("Failed to retrieve memories for namespace %s: %s", namespace, e)
        if all_context:
            context_text = "\n".join(all_context)
            messages[-1]["content"].insert(
                0, {"text": f"<{self.config.context_tag}>{context_text}</{self.config.context_tag}>"}
            )
            logger.info("Retrieved %s customer context items", len(all_context))
        if pending is not None and pending[0] == question:
            logger.info("Customer context came from the lookup started at the beginning of the request")
