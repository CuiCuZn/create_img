"""一次性脚本:用 Playwright 从 perchance 原站抓取完整风格预设(86+ 风格)。

perchance 生成器的风格定义流程:
  1. 主页面 <script id="preloaded-generator-data"> 引用 {import:t2i-styles}
  2. 实际风格定义在 <script id="imported-generators"> 中名为 t2i-styles 的
     generator 的 modelText 字段里(perchance DSL,URL 编码的 JSON)
  3. DSL 结构:$output 块下,每个风格名顶格(2 空格缩进),
     其下 prompt/negative(4 空格缩进),prompt 可能是多行(6 空格缩进正文)

本脚本完成:浏览器抓取 -> 解码 -> DSL 解析 -> 清理变量引用 -> 写入 art_styles.json。

用法:
    cd backend
    python -m scripts.extract_styles            # 抓取并覆盖 app/data/art_styles.json
    python -m scripts.extract_styles --print     # 仅打印结果,不写文件

注意:依赖 perchance 页面结构,原站改版可能需调整解析逻辑。
抓取失败时不会覆盖已有的 art_styles.json。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

GENERATOR_URL = "https://perchance.org/ai-character-generator"
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "app" / "data" / "art_styles.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


async def fetch_imported_generators() -> str:
    """用浏览器打开生成器,提取 imported-generators 脚本内容并解码。"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        print(f"打开 {GENERATOR_URL} ...")
        await page.goto(GENERATOR_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)

        raw = await page.evaluate(
            """() => {
                const el = document.getElementById('imported-generators');
                return el ? el.textContent : null;
            }"""
        )
        await browser.close()

    if not raw:
        raise RuntimeError("未找到 imported-generators,页面结构可能已变化。")
    decoded = unquote(raw)
    print(f"imported-generators 解码后长度: {len(decoded)}")
    return decoded


def extract_t2i_styles_modeltext(decoded: str) -> str:
    """从 imported-generators 中提取 t2i-styles 的 modelText 并反转义。"""
    marker = '"name":"t2i-styles","modelText":"'
    start = decoded.find(marker)
    if start < 0:
        raise RuntimeError("未找到 t2i-styles 生成器定义。")
    content_start = start + len(marker)
    end_marker = '","imports":[]'
    end = decoded.find(end_marker, content_start)
    if end < 0:
        # 兜底:找 lastEditTime
        end = decoded.find('","lastEditTime', content_start)
    if end < 0:
        raise RuntimeError("无法定位 t2i-styles modelText 结束位置。")

    text = decoded[content_start:end]
    # 反转义 JSON 字符串转义
    text = text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return text


def parse_styles(model_text: str) -> list[dict[str, str]]:
    """解析 perchance DSL,提取风格列表。

    结构:
      $output
        <风格名>           (2 空格缩进)
          prompt = ...     (4 空格,单行) 或 prompt 后跟 6 空格正文(多行)
          negative = ...   (4 空格)
    """
    lines = model_text.split("\n")
    output_idx = next((i for i, l in enumerate(lines) if l.strip() == "$output"), -1)
    if output_idx < 0:
        raise RuntimeError("DSL 中未找到 $output 块。")
    style_lines = lines[output_idx + 1:]

    styles: list[dict[str, str]] = []
    i = 0
    while i < len(style_lines):
        line = style_lines[i]
        indent = len(line) - len(line.lstrip())
        trimmed = line.strip()

        # 风格名:2 空格缩进,非空非注释,不含 =,非已知属性/修饰词关键字
        # shot/color/effect/style/genre 等是 modifiers 子块(也是 2 空格缩进),需排除
        if (
            indent == 2
            and trimmed
            and not trimmed.startswith("//")
            and "=" not in trimmed
            and trimmed not in {
                "prompt", "negative", "modifiers", "meta:tags", "meta:import",
                # 修饰词子节点(photoModifiers / animeModifiers 等的成员)
                "shot", "color", "effect", "style", "genre", "lighting",
            }
        ):
            name = trimmed.split("//")[0].strip()
            if not name:
                i += 1
                continue

            prompt_text, negative_text = _read_style_props(style_lines, i)
            styles.append(
                {
                    "name": name,
                    "positive_prefix": _clean(prompt_text),
                    "negative_prefix": _clean(negative_text),
                }
            )
        i += 1

    return styles


def _read_style_props(style_lines: list[str], style_idx: int) -> tuple[str, str]:
    """读取一个风格块内的 prompt / negative 属性。"""
    prompt_text = ""
    negative_text = ""
    j = style_idx + 1
    while j < len(style_lines):
        sub = style_lines[j]
        sub_indent = len(sub) - len(sub.lstrip())
        sub_trim = sub.strip()
        if sub_indent <= 2 and sub_trim:
            break  # 进入下一个风格

        if sub_indent == 4:
            if sub_trim.startswith("prompt ="):
                prompt_text = re.sub(r"^prompt\s*=\s*", "", sub_trim)
            elif sub_trim == "prompt" or sub_trim.startswith("prompt //") or sub_trim.startswith("prompt  "):
                # 多行 prompt:收集 6 空格缩进的正文
                parts: list[str] = []
                k = j + 1
                while k < len(style_lines):
                    ml = style_lines[k]
                    ml_indent = len(ml) - len(ml.lstrip())
                    ml_trim = ml.strip()
                    if ml_indent <= 4 and ml_trim:
                        break
                    if ml_trim and ml_indent >= 6 and not ml_trim.startswith("//"):
                        parts.append(ml_trim)
                    k += 1
                prompt_text = " ".join(parts)
            elif sub_trim.startswith("negative ="):
                negative_text = re.sub(r"^negative\s*=\s*", "", sub_trim)
        j += 1
    return prompt_text, negative_text


def _clean(text: str) -> str:
    """清理 prompt 文本:去掉 perchance 变量引用与残留破碎句。"""
    if not text:
        return ""
    # 去掉变量引用 [input.description...] / [input.negative...] / [其它]
    text = re.sub(r"\[input\.description[^\]]*\]\s*,?\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[input\.negative[^\]]*\]\s*,?\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = text.replace("^2", " ")
    text = re.sub(r"\\+$", "", text)  # 尾部反斜杠
    # 清理删变量后残留的破碎句子
    text = re.sub(r"\b[A-Z][a-z]+(?:\s+[a-z-]+)*\s+(?:photo|photograph|real-life photograph)\s+of\s+\.", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bA\s+(?:It's|casual photo)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bof\s+\.", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bA\s+\.", "", text, flags=re.IGNORECASE)
    # 合并空白与标点
    text = re.sub(r"\s+\.\s*", ". ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"^[\s,.]+", "", text).strip()
    text = re.sub(r"[\s,]+$", "", text).strip()
    text = re.sub(r"^\.+\s*", "", text).strip()
    return text


async def main() -> int:
    parser = argparse.ArgumentParser(description="从 perchance 原站抓取完整风格预设")
    parser.add_argument("--print", action="store_true", help="仅打印结果,不写文件")
    args = parser.parse_args()

    try:
        decoded = await fetch_imported_generators()
        model_text = extract_t2i_styles_modeltext(decoded)
        styles = parse_styles(model_text)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    if not styles:
        print("错误: 未能解析出任何风格。", file=sys.stderr)
        return 1

    # 补 None 风格在首位
    if not any(s["name"].lower() == "none" for s in styles):
        styles.insert(0, {"name": "None", "positive_prefix": "", "negative_prefix": ""})

    print(f"\n解析到 {len(styles)} 个风格(含 None):")
    for s in styles[:6]:
        pos = s["positive_prefix"][:55]
        print(f"  - {s['name']}: {pos}...")
    print(f"  ... 共 {len(styles)} 个")

    if args.print:
        print("\n" + json.dumps(styles, ensure_ascii=False, indent=2))
        return 0

    OUTPUT_FILE.write_text(json.dumps(styles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUTPUT_FILE}({len(styles)} 个风格)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
