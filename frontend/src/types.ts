/** 前端类型定义,与后端 schemas.py 对应。 */

export type Shape = "portrait" | "square" | "landscape";

/** 风格预设。 */
export interface ArtStyle {
  name: string;
  positive_prefix: string;
  negative_prefix: string;
}

/** 生图请求。 */
export interface GenerateRequest {
  prompt: string;
  negative_prompt?: string;
  seed?: number;
  shape?: Shape;
  guidance_scale?: number;
  style?: string;
  /** 图生图参考图:base64 data URL 或 http URL。为空则文生图。 */
  reference_image?: string | null;
  /** 生成图片数量,1-4。 */
  count?: number;
}

/** 单张生成图片。 */
export interface ImageItem {
  /** base64 编码图片(不含 data: 前缀)。 */
  image: string;
  file_extension: string;
  seed: number;
  width: number;
  height: number;
  maybe_nsfw: boolean;
}

/** 生图响应。 */
export interface GenerateResponse {
  /** 生成的图片列表。 */
  images: ImageItem[];
  prompt: string;
  negative_prompt: string;
  style: string;
}
