"""Make the tools that change a customer's record something the assistant can only use when asked.

The system prompt asks the assistant to confirm with the advisor before it
creates or changes a profile, or creates, changes or deletes a third-party
policy. Models still get this wrong some of the time, and text the model reads
along the way (a web page, an uploaded document, a note, a remembered fact) can
push it to write. So the rule is also enforced here, in code, before the tool
runs, the same way `note_guard` does it for `add_note`.

A guarded tool is allowed when:

* the advisor's own words in the current message ask for that kind of change
  ("update her phone to ...", "add this policy", "delete the BigRival policy",
  "create a profile for Sam"), in English or in any language the app offers, or
* the advisor answers "yes" (or similar) right after the assistant read the
  change back or offered to make it, or
* for `create_profile` only, the session is a new-prospect onboarding session
  (no customer selected), where creating the record is the first step, or
* for `update_profile` only, the profile was created earlier in this
  conversation, which is how a prospect's record is filled in as the advisor
  answers the assistant's questions.

Otherwise the call is cancelled with a message that tells the model what to do
instead: say what it would change, then ask. Nothing is written. Text that
reached the model from a tool result, memory or a document is never the
advisor's words, so it cannot satisfy any of these.

The text agent and the voice agent use the same file (the voice agent keeps a
copy: keep the two in sync). Strands fires the same `BeforeToolCallEvent` for both.

Form filling (`open_application_form`, `fill_form_field`) is not guarded here:
it writes nothing to a customer's record. It only puts suggested values on the
advisor's screen, each with a confidence and the words it came from, and the
advisor reviews and submits the form themselves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from note_guard import advisor_text, current_user_message, _texts

_FLAGS = re.IGNORECASE

# Words that lead into a request: "please update", "can you update", "and update".
_LEAD_IN = (
    r"(?:please|pls|kindly|can you|could you|would you|will you|go ahead and|and then|then|now|also|just|"
    r"let'?s|i want you to|i'd like you to|i need you to|you can|you should|you need to|i'd like to)"
)
_NEGATION = re.compile(
    r"\b(don'?t|do not|dont|never|not|no need to|without|stop|avoid|shouldn'?t|should not|cannot|can'?t)\b[^.\n]{0,25}$",
    _FLAGS,
)
_PASSIVE = re.compile(
    r"\b(?:be|get|got|been)\s+(?:updated|changed|saved|added|corrected|fixed|recorded|stored|deleted|removed|dropped|created|set|marked)\b",
    _FLAGS,
)
_WINDOW = 60


def _has_intent(text: str, verbs: str, nouns: str) -> bool:
    """True if `text` asks for the action: a verb close to a noun, or the verb leading a sentence.

    A verb that is negated ("don't update") does not count. "update me" and
    "update us" are requests for information, not for a change.
    """
    if not text:
        return False
    verb_re = re.compile(rf"\b(?:{verbs})\b(?!\s+(?:me|us|on|about)\b)", _FLAGS)
    noun_re = re.compile(rf"\b(?:{nouns})", _FLAGS)
    lead_re = re.compile(rf"(?:[.!?\n;:]\s*|\b{_LEAD_IN}\s+)$", _FLAGS)
    for m in verb_re.finditer(text):
        prefix = text[:m.start()]
        if _NEGATION.search(prefix[-40:]):
            continue
        # The verb opens the message, a sentence or a request ("Update her phone ...").
        if not prefix.strip() or lead_re.search(prefix[-40:]):
            return True
        window = text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
        if noun_re.search(window):
            return True
    for m in _PASSIVE.finditer(text):
        window = text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
        if noun_re.search(window) and not _NEGATION.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


_AFFIRM = re.compile(
    r"^\s*[\"'*_\[(]*\s*(yes|yeah|yep|yup|please do|please go ahead|go ahead|do it|sounds good|looks good|"
    r"all good|all (the )?fields look good|that'?s (right|correct)|confirm(ed)?|affirmative|absolutely|of course|"
    r"add it|save it|update it|create it|delete it|remove it|go for it|that'?s fine|that is fine|that works|"
    r"works for me|fine,? go|proceed(?!\s+with\b)|do that|yes do|do so)\b",
    _FLAGS,
)
# Short replies that only count as a "yes" when they are the whole answer.
_AFFIRM_SHORT = re.compile(r"^\s*[\"'*_\[(]*\s*(ok|okay|sure|right|correct|exactly|perfect|fine)\b", _FLAGS)
_HOLD = re.compile(
    r"\b(don'?t|do not|not yet|wait|hold on|hold off|stop|never|no need|cancel|actually no|wrong|incorrect|"
    r"not (right|correct|quite|exactly))\b",
    _FLAGS,
)
# The assistant's turn before "yes" has to ask or offer, not just state something.
_OFFER = re.compile(
    r"\?|\b(want me|shall i|should i|would you like|do you want|can i|may i|ok to|okay to|go ahead|confirm|"
    r"is that (right|correct)|does that look|look(s)? (right|good|correct)|proceed|let me know)\b",
    _FLAGS,
)


def _affirms(text: str) -> bool:
    if not text or _HOLD.search(text):
        return False
    return bool(_AFFIRM.match(text)) or (bool(_AFFIRM_SHORT.match(text)) and len(text.split()) <= 3)


# ---------------------------------------------------------------------------
# Other languages
#
# The app and both assistants run in more than English. For each language the
# guard knows the verbs that ask for a create / change / delete, the nouns for a
# profile, a profile field and a policy, the words that mean "yes" at the start
# of a reply, and the words that hold a request back. A request needs a verb and
# a noun of the same language close together with no hold word between them. A
# "yes" needs a reply that starts with an affirmation, has no hold word and is
# not itself a question.
# ---------------------------------------------------------------------------


def _alt(*parts: str) -> str:
    return "|".join(parts)


@dataclass(frozen=True)
class _Language:
    code: str
    suffix: str
    create: str
    change: str
    delete: str
    profile: str
    fields: str
    policy: str
    affirm: str
    hold: str
    spaced: bool = True  # words are separated by spaces (False for Japanese, Korean, Chinese, Thai)
    compounds: bool = False  # nouns appear inside longer words (German)


def _compile_language(lang: _Language) -> dict[str, re.Pattern]:
    def verbs(stems: str) -> re.Pattern:
        if lang.spaced:
            return re.compile(rf"\b(?:{stems})(?:{lang.suffix})?\b" if lang.suffix else rf"\b(?:{stems})\b", _FLAGS)
        return re.compile(stems, _FLAGS)

    def nouns(words: str) -> re.Pattern:
        if lang.spaced and not lang.compounds:
            return re.compile(rf"\b(?:{words})", _FLAGS)
        return re.compile(words, _FLAGS)

    word_end = r"(?:\b|(?=[\s,.!;:]|$))" if lang.spaced else ""
    lead = r"^\s*[\"'*_\[(]*\s*"
    affirm = re.compile(rf"{lead}(?:{lang.affirm}){word_end}", _FLAGS)
    hold = re.compile(rf"\b(?:{lang.hold})\b" if lang.spaced else lang.hold, _FLAGS)
    return {
        "create": verbs(lang.create),
        "change": verbs(lang.change),
        "delete": verbs(lang.delete),
        "profile": nouns(lang.profile),
        "fields": nouns(lang.fields),
        "policy": nouns(lang.policy),
        "affirm": affirm,
        "hold": hold,
    }


_LANGUAGES = (
    _Language(
        "de", r"e|en|n|t",
        create=_alt("erstell", "anleg", "leg", "hinzufüg", "füg", "speicher", "registrier", "erfass", "eintrag", "trag"),
        change=_alt("aktualisier", "änder", "bearbeit", "korrigier", "setz", "anpass", "ersetz", "speicher", "trag", "eintrag",
                    "vermerk", "markier", "ergänz", "erneuer", "hinzufüg", "füg", "leg"),
        delete=_alt("lösch", "entfern", "streich", "stornier", "verwerf"),
        profile=_alt("profil", "kunde", "kundin", "interessent", "datensatz", "akte", "stammdaten", "person", "kontakt"),
        fields=_alt("telefon", "nummer", "handy", "e-?mail", "adresse", "einkommen", "gehalt", "beruf", "arbeitgeber",
                    "familienstand", "verheiratet", "ledig", "alter", "geburt", "kinder", "raucher"),
        policy=_alt("polic", "versicherung", "vertrag", "deckung", "prämie", "beitrag", "verlängerung", "laufzeit", "erneuerung"),
        affirm=_alt("ja", "jawohl", "jep", "genau", "klar", "gerne", "gern", "mach das", "mach es", "machen sie", "in ordnung",
                    "einverstanden", "bestätigt", "richtig", "stimmt", "passt", "perfekt", "los", "tu es", "leg los"),
        hold=_alt("nicht", r"kein\w*", "ohne", "nie", "niemals", r"warte\w*", r"stopp\w*", "nein", "falsch", "moment"),
        compounds=True,
    ),
    _Language(
        "fr", r"er|e|es|ez|ent|ir|is|issez|re|s",
        create=_alt("cré", "ajout", "enregistr", "inscri", "saisi", "sauvegard"),
        change=_alt("mett", "met", "modifi", "chang", "corrig", "actualis", "ajust", "remplac", "renouvel", "marqu", "enregistr",
                    "ajout", "not", "sauvegard", "saisi", "mod"),
        delete=_alt("supprim", "retir", "effac", "enlev", "annul"),
        profile=_alt("profil", "prospect", "client", "dossier", "fiche", "contact", "personne"),
        fields=_alt("téléphone", "portable", "mobile", "numéro", "e-?mail", "courriel", "adresse", "revenu", "salaire", "profession",
                    "employeur", "situation", "marié", "célibataire", "âge", "naissance", "enfants", "fumeur", "tabac"),
        policy=_alt("polices?", "contrat", "assurance", "assureur", "couverture", "prime", "garantie", "renouvellement", "échéance"),
        affirm=_alt("oui", "ouais", "d'accord", "vas-y", "allez-y", "c'est bon", "c'est correct", "c'est exact",
                    "parfait", "exact", "correct", "confirmé", "je confirme", "bien sûr", "volontiers", "fais-le", "faites-le",
                    "tout à fait", "entendu"),
        hold=_alt("pas", "jamais", "sans", "non", r"attend\w*", r"arrêt\w*", "stop"),
    ),
    _Language(
        "es", r"ar|er|ir|a|e|an|en|ad|ed|id|ga|ue|que|gue",
        create=_alt("cre", "añad", "agreg", "registr", "guard", "ingres", "anot"),
        change=_alt("actualiz", "actualic", "cambi", "modific", "edit", "corrig", "ajust", "reemplaz", "renov", "marc", "marqu", "pon", "pong",
                    "establec", "guard", "anot", "registr", "agreg", "añad", "sustitu"),
        delete=_alt("elimin", "borr", "quit", "suprim", "descart", "cancel"),
        profile=_alt("perfil", "prospecto", "cliente", "registro", "ficha", "contacto", "persona"),
        fields=_alt("teléfono", "telefono", "móvil", "celular", "número", "correo", "e-?mail", "dirección", "ingresos", "salario",
                    "sueldo", "ocupación", "profesión", "empleador", "estado civil", "casad", "soltero", "edad", "nacimiento",
                    "hijos", "fumador", "tabaco"),
        policy=_alt("póliza", "poliza", "seguro", "aseguradora", "cobertura", "prima", "renovación", "vigencia", "contrato"),
        affirm=_alt(r"s[ií](?=\s*(?:[,.!]|$))", "claro", "adelante", "de acuerdo", "correcto", "perfecto", "exacto", "hazlo",
                    "háganlo", "confirmado", "confirmo", "vale", "dale", "por supuesto", "desde luego", "así es",
                    "está bien", "esta bien", "procede", "proceda", "sigue"),
        hold=_alt("no", "nunca", "sin", r"espera\w*", "detente", "aún no", "todavía", "incorrecto"),
    ),
    _Language(
        "it", r"are|ere|ire|a|i|e|hi",
        create=_alt("cre", "aggiung", "registr", "salv", "inserisc", "inser"),
        change=_alt("aggiorn", "modific", "cambi", "corregg", "impost", "sostituisc", "sostitu", "rinnov", "segn", "salv",
                    "inserisc", "inser", "aggiung"),
        delete=_alt("elimin", "cancell", "rimuov", "rimu", "togli", "tolg"),
        profile=_alt("profilo", "prospect", "cliente", "scheda", "contatto", "persona"),
        fields=_alt("telefono", "cellulare", "numero", "e-?mail", "indirizzo", "reddito", "stipendio", "professione", "datore",
                    "stato civile", "sposat", "celibe", "nubile", "età", "nascita", "figli", "fumatore"),
        policy=_alt("polizza", "assicurazione", "assicuratore", "copertura", "premio", "rinnovo", "scadenza", "contratto"),
        affirm=_alt(r"s[ìi](?=\s*(?:[,.!]|$))", "certo", "esatto", "giusto", "corretto", "va bene", "procedi", "fallo",
                    "confermo", "confermato", "perfetto", "d'accordo"),
        hold=_alt("non", "mai", "senza", r"aspett\w*", "ferma", "fermati", "sbagliato"),
    ),
    _Language(
        "pt", r"ar|er|ir|e|a|ue|que|em",
        create=_alt("cri", "adicion", "registr", "salv", "cadastr", "inclu"),
        change=_alt("atualiz", "alter", "mud", "edit", "corrig", "ajust", "substitu", "renov", "marc", "marqu", "defin", "salv",
                    "registr", "adicion"),
        delete=_alt("exclu", "apag", "remov", "delet", "elimin", "cancel"),
        profile=_alt("perfil", "prospect", "cliente", "registro", "cadastro", "ficha", "contato", "pessoa"),
        fields=_alt("telefone", "celular", "número", "e-?mail", "endereço", "renda", "salário", "ocupação", "profissão",
                    "empregador", "estado civil", "casad", "solteir", "idade", "nascimento", "filhos", "fumante"),
        policy=_alt("apólice", "apolice", "seguro", "seguradora", "cobertura", "prêmio", "renovação", "contrato"),
        affirm=_alt("sim", "claro", "certo", "correto", "exato", "tudo bem", "pode", "faça", "confirmo", "confirmado",
                    "perfeito", "de acordo", "vá em frente"),
        hold=_alt("não", "nao", "nunca", "sem", r"espera\w*", "pare", r"aguarde\w*", "errado"),
    ),
    _Language(
        "ms", r"kan|lah",
        create=_alt("cipta", "mencipta", "buat", "membuat", "tambah", "menambah", "daftar", "mendaftar", "simpan", "menyimpan",
                    "masukkan", "memasukkan", "rekod", "merekod"),
        change=_alt(r"kemas\s*kini", r"mengemas\s*kini", "ubah", "mengubah", "tukar", "menukar", "edit", "mengedit", "betulkan",
                    "membetulkan", "baiki", "membaiki", "tetapkan", "menetapkan", "ganti", "mengganti", "perbaharui",
                    "memperbaharui", "tanda", "menanda", "simpan", "menyimpan", "tambah", "menambah", "masukkan", "memasukkan",
                    "rekod", "merekod", "catat", "mencatat"),
        delete=_alt("padam", "memadam", "buang", "membuang", "batal", "membatal", "singkir", "menyingkir"),
        profile=_alt("profil", "prospek", "pelanggan", "klien", "rekod", "fail", "kenalan", "orang"),
        fields=_alt("telefon", "nombor", "e-?mel", "alamat", "pendapatan", "gaji", "pekerjaan", "majikan", "perkahwinan",
                    "berkahwin", "bujang", "umur", "lahir", "anak", "perokok", "merokok"),
        policy=_alt("polisi", "insurans", "perlindungan", "pembaharuan", "premium"),
        affirm=_alt("ya", "baik", "boleh", "teruskan", "setuju", "betul", "tepat", "sahkan", "okey", "sudah tentu", "silakan", "sila"),
        hold=_alt("jangan", "tidak", "belum", "tunggu", "berhenti", "tanpa", "bukan", "salah"),
    ),
    _Language(
        "id", r"kan|lah",
        create=_alt("buat", "membuat", "tambah", "menambah", "daftar", "mendaftar", "simpan", "menyimpan", "masukkan",
                    "memasukkan", "catat", "mencatat", "input", "menginput"),
        change=_alt("perbarui", "memperbarui", "mutakhirkan", "memutakhirkan", "ubah", "mengubah", "ganti", "mengganti", "edit",
                    "mengedit", "koreksi", "mengoreksi", "perbaiki", "memperbaiki", "atur", "mengatur", "setel", "menyetel",
                    "tetapkan", "menetapkan", "tandai", "menandai", "simpan", "menyimpan", "tambah", "menambah", "masukkan",
                    "memasukkan", "catat", "mencatat", "perpanjang", "memperpanjang"),
        delete=_alt("hapus", "menghapus", "buang", "membuang", "batal", "membatalkan", "singkir", "menyingkirkan"),
        profile=_alt("profil", "prospek", "nasabah", "pelanggan", "klien", "kontak", "orang"),
        fields=_alt("telepon", "ponsel", "nomor", "e-?mail", "alamat", "pendapatan", "gaji", "penghasilan", "pekerjaan",
                    "pemberi kerja", "pernikahan", "menikah", "lajang", "usia", "umur", "lahir", "anak", "perokok", "merokok"),
        policy=_alt("polis", "asuransi", "pertanggungan", "perpanjangan", "premi"),
        affirm=_alt("ya", "iya", "baik", "boleh", "lanjutkan", "setuju", "benar", "tepat", "konfirmasi", "oke", "silakan",
                    "silahkan", "tentu"),
        hold=_alt("jangan", "tidak", "belum", "tunggu", "berhenti", "tanpa", "bukan", "salah"),
    ),
    _Language(
        "ja", "",
        create=_alt("作成", "登録", "追加", "新規", "保存", "入力"),
        change=_alt("更新", "変更", "修正", "編集", "訂正", "書き換", "置き換", "設定", "保存", "登録", "追加", "入力", "記録"),
        delete=_alt("削除", "消去", "消して", "取り消", "外して"),
        profile=_alt("プロフィール", "プロファイル", "顧客", "見込み", "プロスペクト", "お客様", "レコード", "情報", "人物"),
        fields=_alt("電話", "携帯", "番号", "メール", "住所", "収入", "年収", "給与", "職業", "勤務先", "婚姻", "結婚", "独身",
                    "年齢", "生年月日", "子供", "扶養", "喫煙", "タバコ"),
        policy=_alt("保険", "証券", "契約", "補償", "保障", "更新日", "満期", "ポリシー"),
        affirm=_alt("はい", "ええ", "うん", "お願い", "大丈夫", "オッケー", "オーケー", "了解", "それで", "実行", "いいです",
                    "良いです", "結構", "その通り", "そうです", "確認しました", "承知"),
        hold=_alt("しないで", "ないで", "やめ", "止め", "ストップ", "いいえ", "まだ", "待って", "違う", "しなくて", "せず", "不要", "ません"),
        spaced=False,
    ),
    _Language(
        "ko", "",
        create=_alt("생성", "만들", "추가", "등록", "저장", "입력"),
        change=_alt("업데이트", "수정", "변경", "바꿔", "바꾸", "편집", "정정", "설정", "저장", "등록", "추가", "입력", "갱신", "기록"),
        delete=_alt("삭제", "지워", "제거", "취소"),
        profile=_alt("프로필", "고객", "가망", "잠재", "프로스펙트", "레코드", "정보", "인물"),
        fields=_alt("전화", "휴대폰", "번호", "이메일", "주소", "소득", "수입", "연봉", "급여", "직업", "혼인", "결혼", "미혼",
                    "나이", "연령", "생년월일", "자녀", "흡연", "담배"),
        policy=_alt("보험", "증권", "계약", "보장", "만기", "폴리시"),
        affirm=_alt("네", "예", "응", "그래", "좋아", "좋습니다", "맞아", "맞습니다", "확인", "진행", "부탁", "해주세요", "해 주세요",
                    "그렇게", "오케이"),
        hold=_alt("하지 마", "하지마", "말고", "말아", "마세요", "아니", "아직", "잠깐", "기다려", "멈춰", "않"),
        spaced=False,
    ),
    _Language(
        "zh", "",
        create=_alt("创建", "創建", "新建", "建立", "添加", "新增", "登记", "登記", "录入", "錄入", "保存", "儲存"),
        change=_alt("更新", "修改", "更改", "变更", "編輯", "编辑", "纠正", "更正", "调整", "设置", "设为", "改为", "改成", "保存",
                    "录入", "添加", "登记", "记录", "标记", "续保"),
        delete=_alt("删除", "刪除", "移除", "去掉", "清除", "取消", "作废"),
        profile=_alt("档案", "檔案", "资料", "客户", "客戶", "准客户", "个人信息", "信息", "记录", "联系人"),
        fields=_alt("电话", "手机", "号码", "邮箱", "电子邮件", "地址", "收入", "薪水", "工资", "职业", "雇主", "婚姻", "已婚",
                    "单身", "年龄", "出生", "子女", "孩子", "吸烟"),
        policy=_alt("保单", "保險", "保险", "合同", "保障", "保费", "续保", "到期", "承保"),
        affirm=_alt("是的", "是", "好的", "好", "可以", "确认", "確認", "没问题", "沒問題", "对", "對", "行", "同意", "没错", "继续",
                    "请", "請", "麻烦", "就这样", "嗯"),
        hold=_alt("不要", "不用", "别", "別", "不", "先不", "等一下", "等等", "暂时", "停", "不对", "不是", "错了", "錯了"),
        spaced=False,
    ),
    _Language(
        "th", "",
        create=_alt("สร้าง", "เพิ่ม", "ลงทะเบียน", "บันทึก", "เก็บ", "กรอก"),
        change=_alt("อัปเดต", "อัพเดต", "อัพเดท", "แก้ไข", "เปลี่ยน", "ปรับ", "ตั้งค่า", "แทนที่", "ต่ออายุ", "เพิ่ม", "กรอก", "ใส่", "บันทึก"),
        delete=_alt("ลบ", "เอาออก", "ยกเลิก", "นำออก"),
        profile=_alt("โปรไฟล์", "ประวัติ", "ลูกค้า", "ผู้มุ่งหวัง", "ข้อมูล", "บุคคล", "รายชื่อ"),
        fields=_alt("โทรศัพท์", "เบอร์", "มือถือ", "อีเมล", "ที่อยู่", "รายได้", "เงินเดือน", "อาชีพ", "นายจ้าง", "สถานภาพ",
                    "แต่งงาน", "โสด", "อายุ", "เกิด", "บุตร", "สูบบุหรี่", "บุหรี่"),
        policy=_alt("กรมธรรม์", "ประกัน", "ความคุ้มครอง", "เบี้ย", "ครบกำหนด"),
        affirm=_alt("ใช่", "ได้", "ตกลง", "โอเค", "ครับ", "ค่ะ", "คะ", "ยืนยัน", "เอาเลย", "ถูกต้อง", "เห็นด้วย", "ดำเนินการ", "ทำเลย"),
        hold=_alt("ไม่", "อย่า", "ยัง", "รอ", "หยุด", "ผิด", "ห้าม"),
        spaced=False,
    ),
)
_COMPILED = tuple(_compile_language(lang) for lang in _LANGUAGES)

# Which verb kinds and noun kinds make a request for each guarded tool.
_KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "create_profile": (("create",), ("profile",)),
    "update_profile": (("change", "create"), ("profile", "fields")),
    "create_third_party_policy": (("create",), ("policy",)),
    "update_third_party_policy": (("change",), ("policy",)),
    "delete_third_party_policy": (("delete",), ("policy",)),
}
_QUESTION = re.compile(r"[?？]|ไหม|มั้ย|หรือไม่|หรือเปล่า|ですか|ますか|까요|습니까|니까|吗|嗎", _FLAGS)
_ASKS = re.compile(r"[?？]")


def _language_intent(text: str, tool: str) -> bool:
    """True if `text` asks for the change in one of the supported languages."""
    kinds = _KINDS.get(tool)
    if not text or kinds is None:
        return False
    verb_kinds, noun_kinds = kinds
    for patterns in _COMPILED:
        for kind in verb_kinds:
            for m in patterns[kind].finditer(text):
                if patterns["hold"].search(text[max(0, m.start() - 40):m.end() + 25]):
                    continue
                lo, hi = max(0, m.start() - _WINDOW), m.end() + _WINDOW
                for noun_kind in noun_kinds:
                    for n in patterns[noun_kind].finditer(text, lo, hi):
                        if n.end() <= m.start() or n.start() >= m.end():
                            return True
    return False


def _language_affirms(text: str) -> bool:
    """True if `text` is a plain "yes" in one of the supported languages."""
    if not text or _ASKS.search(text):
        return False
    return any(not p["hold"].search(text) and p["affirm"].match(text) for p in _COMPILED)


def _language_offer(prev: str, tool: str) -> bool:
    """True if the assistant's text asks whether to make this kind of change, in a supported language."""
    return bool(_QUESTION.search(prev)) and _language_intent(prev, tool)


@dataclass(frozen=True)
class Rule:
    tool: str
    verbs: str
    nouns: str
    # Words the assistant's read-back or offer has to contain for "yes" to answer it.
    topic: re.Pattern
    cancel: str


def _cancel(what: str) -> str:
    return (
        "Not done. The advisor has not asked for this in their latest message and has not confirmed it, "
        f"so nothing was changed. Tell the advisor what you would {what}, then ask once: "
        "\"Want me to go ahead?\" Do it only after they say yes."
    )


# "record" is a verb here only when it is not used as a noun ("her record", "the record").
_NOT_A_NOUN = r"(?<!\bthe )(?<!\bher )(?<!\bhis )(?<!\btheir )(?<!\bmy )(?<!\ba )(?<!\bits )(?<!\bany )(?<!\bthat )"

RULES: dict[str, Rule] = {
    "create_profile": Rule(
        tool="create_profile",
        verbs=r"create|add|register|set up|setup|save|enter|onboard|" + _NOT_A_NOUN + r"record",
        nouns=r"profile|prospect|record|customer|client|lead|contact|person|her\b|him\b|them\b|file\b|account",
        topic=re.compile(r"profile|prospect|record|customer|client|register", _FLAGS),
        cancel=_cancel("save as a new profile (name and any details)"),
    ),
    "update_profile": Rule(
        tool="update_profile",
        verbs=r"update|change|edit|modify|correct|fix|amend|revise|adjust|set|mark|save|store|add|put|enter|overwrite|replace|"
        + _NOT_A_NOUN + r"record",
        nouns=(
            r"profile|record|details?\b|info\b|information|phone|mobile|cell\b|number|e-?mail|address|income|salary|earns?\b|"
            r"occupation|job\b|employer|employment|name\b|age\b|birth|dob\b|marital|married|single\b|divorced|widowed|dependents?|"
            r"kids?\b|children|smok|tobacco|risk|objective|horizon|liquidity|home\b|owner|reviewed|review\b|status"
        ),
        topic=re.compile(
            r"profile|record|update|updating|change|details|information|phone|email|address|income|"
            r"occupation|name\b|age\b|status|reviewed|marital|dependents?|smok|tobacco",
            _FLAGS,
        ),
        cancel=_cancel("change on the profile (the fields and their new values)"),
    ),
    "create_third_party_policy": Rule(
        tool="create_third_party_policy",
        verbs=r"add|create|save|enter|register|track|store|put|import|" + _NOT_A_NOUN + r"record",
        nouns=(
            r"polic|coverage|cover\b|insurance|insurer|carrier|document|pdf|upload|extract|fields?\b|details\b|"
            r"it\b|this\b|that\b|these\b|them\b|plan\b|existing"
        ),
        topic=re.compile(r"polic|coverage|insurer|carrier|extract|fields?\b|premium|document", _FLAGS),
        cancel=_cancel("add as a third-party policy (customer, type, insurer and key fields)"),
    ),
    "update_third_party_policy": Rule(
        tool="update_third_party_policy",
        verbs=r"update|change|edit|modify|correct|fix|amend|revise|adjust|set|mark|renew|replace|overwrite|save",
        nouns=r"polic|coverage|cover\b|premium|insurer|carrier|renewal|status|amount|date\b|product|it\b|this\b|that\b|plan\b",
        topic=re.compile(
            r"(?=.*\b(update|change|set|correct|amend|revise|adjust|mark|renew|replace|edit)\b)"
            r"(?=.*(polic|insurer|carrier|premium|coverage|renewal|status|date\b))",
            _FLAGS | re.DOTALL,
        ),
        cancel=_cancel("change on the third-party policy (the policy and the new values)"),
    ),
    "delete_third_party_policy": Rule(
        tool="delete_third_party_policy",
        verbs=r"delete|remove|drop|erase|discard|get rid of|take off|cancel|clear",
        nouns=r"polic|coverage|cover\b|record|entry|insurance|plan\b|it\b|this\b|that\b|one\b|them\b",
        topic=re.compile(
            r"(?=.*\b(delete|remove|erase|drop|discard|get rid)\b)(?=.*(polic|record|entry|coverage))",
            _FLAGS | re.DOTALL,
        ),
        cancel=_cancel("delete (the policy, by insurer and type)"),
    ),
}

PROSPECT_MARKER = "no customer selected"


def short_name(name: Any) -> str:
    """The tool's own name: the gateway prefixes the target ("<target>___<tool>")."""
    return str(name or "").rsplit("___", 1)[-1]


def _assistant_text_before(messages: list[Any], idx: int) -> str:
    for j in range(idx - 1, -1, -1):
        m = messages[j]
        if isinstance(m, dict) and m.get("role") == "assistant":
            text = " ".join(_texts(m)).strip()
            if text:
                return text
    return ""


def _tool_result_texts(messages: list[Any]) -> dict[str, str]:
    """toolUseId -> the text of its result(s), for every tool result in the conversation."""
    out: dict[str, list[str]] = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        for block in m.get("content") or []:
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not isinstance(result, dict):
                continue
            parts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict) and isinstance(c.get("text"), str)]
            out.setdefault(str(result.get("toolUseId")), []).extend(parts)
    return {k: "\n".join(v) for k, v in out.items()}


_CUSTOMER_ID_IN_RESULT = re.compile(r"customer_id\W{1,6}([A-Za-z0-9_-]{1,64})", _FLAGS)


def created_customer_ids(messages: list[Any]) -> set[str]:
    """Customer ids returned by `create_profile` calls earlier in this conversation."""
    created: set[str] = set()
    results = _tool_result_texts(messages)
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for block in m.get("content") or []:
            use = block.get("toolUse") if isinstance(block, dict) else None
            if isinstance(use, dict) and short_name(use.get("name")) == "create_profile":
                for found in _CUSTOMER_ID_IN_RESULT.findall(results.get(str(use.get("toolUseId")), "")):
                    created.add(found)
    return created


def write_allowed(
    rule: Rule,
    messages: list[Any],
    tool_input: Any = None,
    prospect_mode: Callable[[], bool] | None = None,
) -> bool:
    """May the guarded tool run now, given the conversation so far?"""
    raw, idx = current_user_message(messages)
    if idx == -1:
        return False
    text = advisor_text(raw)
    if _has_intent(text, rule.verbs, rule.nouns) or _language_intent(text, rule.tool):
        return True
    if (_affirms(text) or _language_affirms(text)) and idx > 0:
        # "yes" only counts as an answer to a read-back or an offer about this kind of change.
        prev = _assistant_text_before(messages, idx)
        if prev and ((_OFFER.search(prev) and rule.topic.search(prev)) or _language_offer(prev, rule.tool)):
            return True
    if rule.tool == "create_profile":
        in_prospect = prospect_mode() if prospect_mode is not None else PROSPECT_MARKER in raw.lower()
        # With a document attached the assistant is to propose the profile first (see the system prompt).
        if in_prospect and text and "[attached document" not in raw.lower():
            return True
    if rule.tool == "update_profile":
        customer_id = (tool_input or {}).get("customer_id") if isinstance(tool_input, dict) else None
        if isinstance(customer_id, str) and customer_id in created_customer_ids(messages):
            return True
    return False


def attach_write_guard(agent: Any, prospect_mode: Callable[[], bool] | None = None) -> None:
    """Register the guard on a Strands agent (text or voice).

    `prospect_mode`, when given, says whether no customer is in scope. Without
    it the text agent's own header ("No customer selected ...") is used.
    """
    from strands.hooks import BeforeToolCallEvent

    def before_tool(event: Any) -> None:
        use = getattr(event, "tool_use", None) or {}
        rule = RULES.get(short_name(use.get("name")))
        if rule is None:
            return
        try:
            allowed = write_allowed(rule, list(getattr(agent, "messages", []) or []), use.get("input"), prospect_mode)
        except Exception:  # noqa: BLE001 - if the check itself fails, do not write
            allowed = False
        if not allowed:
            event.cancel_tool = rule.cancel

    agent.hooks.add_callback(BeforeToolCallEvent, before_tool)
