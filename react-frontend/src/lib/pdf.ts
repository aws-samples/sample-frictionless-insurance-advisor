/**
 * One-click PDF export.
 *
 * A printable view (a plain DOM tree built for paper, not the interactive
 * screen) is rendered to images and placed on A4 pages. Rendering to images
 * keeps every UI language correct, including Thai, Chinese and Japanese, with
 * no font files to ship. The trade-off is that the text in the PDF cannot be
 * selected.
 *
 * Mark each logical block of the printable view with `data-pdf-block`. Blocks
 * are rendered one by one and flow onto pages without being cut in half; a
 * block taller than a page is the only thing that gets sliced.
 *
 * html2canvas-pro and jsPDF are loaded when the button is clicked, so they
 * are not part of the app's first load.
 */

const A4_WIDTH_MM = 210;
const A4_HEIGHT_MM = 297;
const MARGIN_MM = 12;
/** Gap left between blocks on the same page. */
const BLOCK_GAP_MM = 2;
/** Render at 2x so text stays sharp when the PDF is zoomed or printed. */
const RENDER_SCALE = 2;

/**
 * Text settings for a printable view.
 *
 * The page is drawn to a canvas, which measures and paints text with its own
 * copy of the font, separately from how the browser laid the text out. The two
 * only agree for a plain static font. The system UI font (San Francisco on Mac)
 * adjusts spacing by size, and the app's own OpenType features and tracking
 * are not applied on a canvas, so text came out with squeezed spaces and
 * overlapping letters. A static font and switched-off typographic features
 * keep layout and paint identical. Other scripts (Thai, Chinese, Japanese) fall
 * back to the system font for those characters, as before.
 */
export const PDF_TEXT_STYLE = {
  fontFamily: 'Arial, "Helvetica Neue", Helvetica, "Liberation Sans", sans-serif',
  fontFeatureSettings: 'normal',
  fontVariantLigatures: 'none',
  fontKerning: 'none',
  fontOpticalSizing: 'none',
  fontSynthesis: 'none',
  letterSpacing: 'normal',
  wordSpacing: 'normal',
  textRendering: 'geometricPrecision',
  WebkitFontSmoothing: 'auto',
  boxSizing: 'border-box',
  overflowWrap: 'anywhere',
} as const;

export interface PdfExportOptions {
  /** File name, with .pdf. */
  filename: string;
}

/** Width in CSS pixels at which the printable view should be laid out. */
export const PDF_CONTENT_WIDTH_PX = 794;

export async function exportBlocksToPdf(
  container: HTMLElement,
  { filename }: PdfExportOptions
): Promise<void> {
  const blocks = Array.from(container.querySelectorAll<HTMLElement>('[data-pdf-block]'));
  if (blocks.length === 0) {
    throw new Error('Nothing to export');
  }

  const [{ default: html2canvas }, { jsPDF }] = await Promise.all([
    import('html2canvas-pro'),
    import('jspdf'),
  ]);
  // Fonts still loading would be measured one way and painted another.
  await document.fonts?.ready;

  const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });
  const contentWidth = A4_WIDTH_MM - MARGIN_MM * 2;
  const pageBottom = A4_HEIGHT_MM - MARGIN_MM;
  let y = MARGIN_MM;

  for (const block of blocks) {
    const canvas = await html2canvas(block, {
      scale: RENDER_SCALE,
      backgroundColor: '#ffffff',
      logging: false,
    });
    if (canvas.width === 0 || canvas.height === 0) continue;

    const heightMm = (canvas.height / canvas.width) * contentWidth;
    const pageContentHeight = pageBottom - MARGIN_MM;

    if (heightMm <= pageContentHeight) {
      // Fits on one page: start a new page if it doesn't fit on this one.
      if (y + heightMm > pageBottom && y > MARGIN_MM) {
        pdf.addPage();
        y = MARGIN_MM;
      }
      pdf.addImage(canvas.toDataURL('image/jpeg', 0.95), 'JPEG', MARGIN_MM, y, contentWidth, heightMm);
      y += heightMm + BLOCK_GAP_MM;
      continue;
    }

    // Taller than a page: slice it into page-sized strips.
    if (y > MARGIN_MM) {
      pdf.addPage();
      y = MARGIN_MM;
    }
    const pxPerMm = canvas.width / contentWidth;
    const stripPx = Math.floor(pageContentHeight * pxPerMm);
    for (let offset = 0; offset < canvas.height; offset += stripPx) {
      const sliceHeight = Math.min(stripPx, canvas.height - offset);
      const strip = document.createElement('canvas');
      strip.width = canvas.width;
      strip.height = sliceHeight;
      strip
        .getContext('2d')
        ?.drawImage(canvas, 0, offset, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight);
      if (offset > 0) {
        pdf.addPage();
        y = MARGIN_MM;
      }
      const sliceMm = sliceHeight / pxPerMm;
      pdf.addImage(strip.toDataURL('image/jpeg', 0.95), 'JPEG', MARGIN_MM, y, contentWidth, sliceMm);
      y += sliceMm + BLOCK_GAP_MM;
    }
  }

  pdf.save(filename);
}

/** A file-name-safe version of a title, e.g. "Awesome Term Life Application" -> "awesome-term-life-application". */
export function slugForFile(text: string): string {
  return (
    text
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
      .replace(/^-+|-+$/g, '') || 'document'
  );
}
