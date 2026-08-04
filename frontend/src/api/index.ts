import axios from "axios";
import type { ArtStyle, GenerateRequest, GenerateResponse } from "../types";

const http = axios.create({
  // dev 模式经 vite 代理转发到后端;生产模式同源直连
  baseURL: "/api",
  // 生图较慢(10-30s+),留足超时
  timeout: 180_000,
});

/** 获取风格预设列表。 */
export async function getStyles(): Promise<ArtStyle[]> {
  const { data } = await http.get<ArtStyle[]>("/styles");
  return data;
}

/** 生图(文生图 / 图生图)。 */
export async function generate(req: GenerateRequest): Promise<GenerateResponse> {
  const { data } = await http.post<GenerateResponse>("/generate", req);
  return data;
}

/** 健康检查。 */
export async function health(): Promise<{ status: string; browser_ready: boolean }> {
  const { data } = await http.get("/health");
  return data;
}

/** 将 base64 图片 + 扩展名拼成可显示/下载的 data URL。 */
export function toDataUrl(base64: string, ext: string): string {
  const mime = ext === "png" ? "image/png" : ext === "webp" ? "image/webp" : "image/jpeg";
  return `data:${mime};base64,${base64}`;
}

/** 把 File 对象转成 base64 data URL(用于图生图参考图上传)。 */
export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
