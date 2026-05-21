from __future__ import annotations

from pathlib import Path
from typing import Any

from .handlers import build_tool_failure, build_tool_success
from .path_utils import resolve_workspace_path

try:
    from markitdown import MarkItDown
except Exception as exc:  # pragma: no cover - 依赖缺失时由工具返回结构化错误
    MarkItDown = None  # type: ignore[assignment]
    _MARKITDOWN_IMPORT_ERROR: Exception | None = exc
else:
    _MARKITDOWN_IMPORT_ERROR = None


SUPPORTED_MARKDOWN_SOURCE_FORMATS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "word",
    ".xlsx": "excel",
    ".xls": "excel",
    ".html": "html",
    ".htm": "html",
}

PREVIEW_MAX_CHARS = 8000


class MarkdownConvertError(Exception):
    """表示可通过调整工具参数修复的 Markdown 转换错误。"""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def _resolve_source(file_path: str) -> tuple[Path, str]:
    raw_path = Path(file_path).expanduser()
    if not raw_path.is_absolute():
        raise MarkdownConvertError(
            "convert_file_to_markdown 只接受绝对路径 filePath。",
            error_code="markdown_convert_source_not_absolute",
        )

    source = resolve_workspace_path(file_path, allow_missing=False)
    if not source.is_file():
        raise MarkdownConvertError(
            f"源路径不是普通文件：{source}",
            error_code="markdown_convert_source_not_file",
        )

    source_format = SUPPORTED_MARKDOWN_SOURCE_FORMATS.get(source.suffix.lower())
    if source_format is None:
        supported = "、".join(sorted(SUPPORTED_MARKDOWN_SOURCE_FORMATS))
        raise MarkdownConvertError(
            f"不支持的文件类型：{source.suffix or '<无后缀>'}。本期仅支持：{supported}。",
            error_code="markdown_convert_unsupported_type",
        )

    return source, source_format


def _resolve_output(source: Path, output_path: str | None) -> Path:
    if output_path is None or not str(output_path).strip():
        target = source.with_suffix(".md").resolve()
    else:
        raw_path = Path(output_path).expanduser()
        if not raw_path.is_absolute():
            raise MarkdownConvertError(
                "convert_file_to_markdown 的 outputPath 只接受绝对路径。",
                error_code="markdown_convert_output_not_absolute",
            )
        target = resolve_workspace_path(output_path, allow_missing=True)

    if target.suffix.lower() != ".md":
        raise MarkdownConvertError(
            f"输出路径必须以 .md 结尾：{target}",
            error_code="markdown_convert_output_not_markdown",
        )
    if target.exists():
        raise MarkdownConvertError(
            f"目标 Markdown 文件已存在：{target}。工具不会覆盖已有文件，请先读取已有文件或指定新的 outputPath。",
            error_code="markdown_convert_output_exists",
        )
    return target


def _extract_markdown_text(result: Any) -> str:
    for attr in ("markdown", "text_content", "text"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value

    if isinstance(result, dict):
        for key in ("markdown", "text_content", "text"):
            value = result.get(key)
            if isinstance(value, str):
                return value

    return str(result)


def _convert_with_markitdown(source: Path) -> str:
    if MarkItDown is None:
        raise MarkdownConvertError(
            f"缺少 markitdown 依赖：{_MARKITDOWN_IMPORT_ERROR}",
            error_code="markdown_convert_dependency_missing",
        )

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert_local(str(source))
    markdown = _extract_markdown_text(result)
    if not markdown.strip():
        raise MarkdownConvertError(
            "MarkItDown 未返回有效 Markdown 内容。",
            error_code="markdown_convert_empty_result",
        )
    return markdown


def run_convert_file_to_markdown(file_path: str, output_path: str | None = None) -> dict[str, Any]:
    try:
        source, source_format = _resolve_source(file_path)
        target = _resolve_output(source, output_path)
        markdown = _convert_with_markitdown(source)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")

        size_bytes = len(markdown.encode("utf-8"))
        preview = markdown[:PREVIEW_MAX_CHARS]
        if len(markdown) > PREVIEW_MAX_CHARS:
            preview += f"\n\n...（Markdown 内容已截断预览，完整内容已写入 {target}）"

        return build_tool_success(
            f"转换成功：{source} -> {target}\n\n{preview}",
            source_path=str(source),
            output_path=str(target),
            source_format=source_format,
            size_bytes=size_bytes,
        )
    except MarkdownConvertError as exc:
        return build_tool_failure(
            f"Error: {exc.message}",
            error_code=exc.error_code,
            error_type=type(exc).__name__,
            filePath=file_path,
            outputPath=output_path,
        )
    except ValueError as exc:
        return build_tool_failure(
            f"Error: {exc}",
            error_code="markdown_convert_path_forbidden",
            error_type=type(exc).__name__,
            filePath=file_path,
            outputPath=output_path,
        )
    except Exception as exc:
        return build_tool_failure(
            f"Error: Markdown 转换失败：{exc}",
            error_code="markdown_convert_failed",
            error_type=type(exc).__name__,
            filePath=file_path,
            outputPath=output_path,
        )
