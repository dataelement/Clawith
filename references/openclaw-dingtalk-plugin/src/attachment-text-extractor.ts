import fs from "node:fs/promises";
import path from "node:path";

const MAX_EXTRACTED_TEXT_CHARS = 6000;
const MAX_ATTACHMENT_EXTRACT_BYTES = 2 * 1024 * 1024;

export interface AttachmentTextExtractionInput {
  path: string;
  mimeType?: string;
  fileName?: string;
}

export interface AttachmentTextExtractionResult {
  text: string;
  truncated: boolean;
  sourceType: "text" | "html" | "pdf" | "docx";
}

async function isFileTooLarge(filePath: string): Promise<boolean> {
  const stat = await fs.stat(filePath);
  return stat.size > MAX_ATTACHMENT_EXTRACT_BYTES;
}

function isTextLikeMimeType(mimeType: string | undefined): boolean {
  if (!mimeType) {
    return false;
  }
  return (
    mimeType.startsWith("text/") ||
    mimeType === "application/json" ||
    mimeType === "application/xml" ||
    mimeType === "application/javascript"
  );
}

function normalizeWhitespace(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .split("\u0000").join("")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function limitExtractedText(text: string): AttachmentTextExtractionResult | null {
  const normalized = normalizeWhitespace(text);
  if (!normalized) {
    return null;
  }
  const truncated = normalized.length > MAX_EXTRACTED_TEXT_CHARS;
  const limited = truncated ? `${normalized.slice(0, MAX_EXTRACTED_TEXT_CHARS)}\n\n[内容已截断]` : normalized;
  return {
    text: limited,
    truncated,
    sourceType: "text",
  };
}

function stripHtml(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&")
    .replace(/&#39;/gi, "'")
    .replace(/&quot;/gi, "\"");
}

async function extractTextLikeFile(filePath: string): Promise<AttachmentTextExtractionResult | null> {
  const raw = await fs.readFile(filePath, "utf8");
  return limitExtractedText(raw);
}

async function extractPdf(filePath: string): Promise<AttachmentTextExtractionResult | null> {
  const pdfParseModule = await import("pdf-parse");
  const fileBuffer = await fs.readFile(filePath);
  const parser = new pdfParseModule.PDFParse({ data: new Uint8Array(fileBuffer) });
  try {
    const result = await parser.getText();
    const limited = limitExtractedText(result.text || "");
    return limited ? { ...limited, sourceType: "pdf" } : null;
  } finally {
    await parser.destroy();
  }
}

async function extractDocx(filePath: string): Promise<AttachmentTextExtractionResult | null> {
  const mammothModule = await import("mammoth");
  const result = await mammothModule.extractRawText({ path: filePath });
  const limited = limitExtractedText(result.value || "");
  return limited ? { ...limited, sourceType: "docx" } : null;
}

async function extractHtml(filePath: string): Promise<AttachmentTextExtractionResult | null> {
  const raw = await fs.readFile(filePath, "utf8");
  const limited = limitExtractedText(stripHtml(raw));
  return limited ? { ...limited, sourceType: "html" } : null;
}

export async function extractAttachmentText(
  input: AttachmentTextExtractionInput,
): Promise<AttachmentTextExtractionResult | null> {
  const mimeType = input.mimeType?.toLowerCase();
  const fileName = (input.fileName || path.basename(input.path)).toLowerCase();

  if (mimeType?.startsWith("image/") || mimeType?.startsWith("audio/") || mimeType?.startsWith("video/")) {
    return null;
  }

  if (await isFileTooLarge(input.path)) {
    return null;
  }

  if (
    mimeType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    fileName.endsWith(".docx")
  ) {
    return extractDocx(input.path);
  }

  if (mimeType === "application/pdf" || fileName.endsWith(".pdf")) {
    return extractPdf(input.path);
  }

  if (mimeType === "text/html" || mimeType === "application/xhtml+xml" || fileName.endsWith(".html") || fileName.endsWith(".htm")) {
    return extractHtml(input.path);
  }

  if (
    isTextLikeMimeType(mimeType) ||
    fileName.endsWith(".txt") ||
    fileName.endsWith(".md") ||
    fileName.endsWith(".markdown") ||
    fileName.endsWith(".csv") ||
    fileName.endsWith(".json") ||
    fileName.endsWith(".xml") ||
    fileName.endsWith(".log")
  ) {
    return extractTextLikeFile(input.path);
  }

  return null;
}
