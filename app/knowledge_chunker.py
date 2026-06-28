from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple


DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_PARENT_CHUNK_SIZE = 4096
DEFAULT_CHILD_CHUNK_SIZE = 384
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", ". ", "; ", "；"]


@dataclass(frozen=True)
class SplitterConfig:
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    separators: Sequence[str] = field(default_factory=lambda: list(DEFAULT_SEPARATORS))
    strategy: str = "auto"
    token_limit: int = 0
    languages: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedChunk:
    content: str
    context_header: str
    seq: int
    start: int
    end: int

    def embedding_content(self) -> str:
        body = self.content.strip()
        if not self.context_header:
            return body
        return f"{self.context_header}\n\n{body}".strip()


@dataclass(frozen=True)
class ParsedChildChunk:
    chunk: ParsedChunk
    parent_index: int


@dataclass(frozen=True)
class ParentChildResult:
    parents: List[ParsedChunk]
    children: List[ParsedChildChunk]


@dataclass(frozen=True)
class DocumentProfile:
    total_chars: int
    line_count: int
    heading_counts: Dict[int, int]
    heading_total: int
    form_feed_count: int
    numbered_heading_count: int
    all_caps_heading_count: int
    visual_separator_count: int

    @property
    def structural_boundary_count(self) -> int:
        return (
            self.form_feed_count
            + self.numbered_heading_count
            + self.all_caps_heading_count
            + self.visual_separator_count
        )

    def dominant_heading_level(self) -> int:
        if not self.heading_counts:
            return 0
        ordered = sorted(self.heading_counts.items(), key=lambda item: (-item[1], item[0]))
        return int(ordered[0][0])


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NUMBERED_HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+){0,5}|[A-Z]\.)\s+.{3,}$")
ALL_CAPS_HEADING_RE = re.compile(r"^\s*[A-Z][A-Z0-9 &/(),:_-]{5,}\s*$")
VISUAL_SEPARATOR_RE = re.compile(r"^\s*(?:[-_=*]{4,}|[-_=*]\s*){4,}\s*$")
EXCESSIVE_BLANKS_RE = re.compile(r"\n{3,}")
FENCE_RE = re.compile(r"^\s*```")

PROTECTED_PATTERNS = [
    re.compile(r"(?s)\$\$.*?\$\$"),
    re.compile(r"!\[[^\]]*\]\([^)]+\)"),
    re.compile(r"\[[^\]]*\]\([^)]+\)"),
    re.compile(r"(?m)^\s*\|.*\|\s*$"),
    re.compile(r"(?s)```(?:\w+)?\s.*?```"),
]


def split(text: str, config: SplitterConfig | None = None) -> List[ParsedChunk]:
    text = _normalize_text(text)
    if not text:
        return []
    cfg = _ensure_config(config or SplitterConfig())
    profile = profile_document(text) if cfg.strategy in {"", "auto"} else None
    chain = _strategy_chain(cfg.strategy, profile)
    last: List[ParsedChunk] = []
    for tier in chain:
        if tier == "heading":
            chunks = _split_by_headings(text, cfg, profile)
        elif tier == "heuristic":
            chunks = _split_by_heuristics(text, cfg)
        else:
            chunks = _split_recursive(text, cfg)
        if _chunks_are_valid(chunks, len(text), cfg.chunk_size):
            return _resequenced(chunks)
        last = chunks
    return _resequenced(last or _split_recursive(text, cfg))


def split_parent_child(
    text: str,
    *,
    parent_config: SplitterConfig | None = None,
    child_config: SplitterConfig | None = None,
) -> ParentChildResult:
    text = _normalize_text(text)
    if not text:
        return ParentChildResult(parents=[], children=[])
    parent_cfg = _ensure_config(
        parent_config
        or SplitterConfig(chunk_size=DEFAULT_PARENT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP, strategy="auto")
    )
    child_cfg = _ensure_config(
        child_config
        or SplitterConfig(chunk_size=DEFAULT_CHILD_CHUNK_SIZE, chunk_overlap=DEFAULT_CHILD_CHUNK_SIZE // 5, strategy="auto")
    )
    parent_chunks = split(text, parent_cfg)
    parents: List[ParsedChunk] = []
    children: List[ParsedChildChunk] = []
    child_seq = 0

    for parent in parent_chunks:
        sub_chunks = split(parent.content, child_cfg)
        parent_index = -1
        if len(sub_chunks) > 1 or (sub_chunks and sub_chunks[0].content != parent.content):
            parent_index = len(parents)
            parents.append(parent)
        for sub in sub_chunks or [parent]:
            merged_header = _merge_breadcrumbs(parent.context_header, sub.context_header)
            child = ParsedChunk(
                content=sub.content,
                context_header=merged_header,
                seq=child_seq,
                start=parent.start + sub.start,
                end=parent.start + sub.end,
            )
            children.append(ParsedChildChunk(chunk=child, parent_index=parent_index))
            child_seq += 1
    return ParentChildResult(parents=_resequenced(parents), children=children)


def profile_document(text: str) -> DocumentProfile:
    text = _normalize_text(text)
    heading_counts: Dict[int, int] = {}
    form_feed_count = text.count("\f")
    numbered_count = 0
    all_caps_count = 0
    visual_count = 0
    in_fence = False
    lines = text.splitlines()
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            heading_counts[level] = heading_counts.get(level, 0) + 1
            continue
        if NUMBERED_HEADING_RE.match(line):
            numbered_count += 1
        elif ALL_CAPS_HEADING_RE.match(line):
            all_caps_count += 1
        elif VISUAL_SEPARATOR_RE.match(line):
            visual_count += 1
    return DocumentProfile(
        total_chars=len(text),
        line_count=len(lines),
        heading_counts=heading_counts,
        heading_total=sum(heading_counts.values()),
        form_feed_count=form_feed_count,
        numbered_heading_count=numbered_count,
        all_caps_heading_count=all_caps_count,
        visual_separator_count=visual_count,
    )


def _split_by_headings(
    text: str,
    cfg: SplitterConfig,
    profile: DocumentProfile | None,
) -> List[ParsedChunk]:
    profile = profile or profile_document(text)
    primary_level = profile.dominant_heading_level()
    if primary_level <= 0:
        return _split_recursive(text, cfg)

    bounds = _heading_boundaries(text, primary_level)
    if len(bounds) <= 1:
        return _split_recursive(text, cfg)

    chunks: List[ParsedChunk] = []
    hierarchy: Dict[int, str] = {}
    for index, (start, heading_line) in enumerate(bounds):
        end = bounds[index + 1][0] if index + 1 < len(bounds) else len(text)
        if heading_line:
            match = HEADING_RE.match(heading_line)
            if match:
                level = len(match.group(1))
                hierarchy = {key: value for key, value in hierarchy.items() if key < level}
                hierarchy[level] = heading_line.strip()
        section = text[start:end]
        breadcrumb = "\n".join(hierarchy[level] for level in sorted(hierarchy))
        if len(section) + len(breadcrumb) + 2 <= cfg.chunk_size:
            chunks.append(ParsedChunk(section, breadcrumb, len(chunks), start, end))
            continue
        sub_chunks = _split_recursive(section, cfg)
        for sub in sub_chunks:
            chunks.append(
                ParsedChunk(
                    content=sub.content,
                    context_header=breadcrumb or sub.context_header,
                    seq=len(chunks),
                    start=start + sub.start,
                    end=start + sub.end,
                )
            )
    return _coalesce_tiny_chunks(chunks, cfg.chunk_size)


def _split_by_heuristics(text: str, cfg: SplitterConfig) -> List[ParsedChunk]:
    if len(text) <= cfg.chunk_size:
        return _split_recursive(text, cfg)
    boundaries = _heuristic_boundaries(text)
    if not boundaries:
        return _split_recursive(text, cfg)
    boundaries = sorted(set([0, *boundaries, len(text)]))
    chunks: List[ParsedChunk] = []
    chunk_start = boundaries[0]
    current_end = chunk_start
    min_chunk = max(50, cfg.chunk_size // 4)
    for boundary in boundaries[1:]:
        block_len = boundary - current_end
        if block_len > cfg.chunk_size:
            if current_end > chunk_start:
                chunks.append(_slice_chunk(text, chunk_start, current_end, len(chunks)))
            chunks.extend(
                _offset_chunks(
                    _split_recursive(text[current_end:boundary], cfg),
                    offset=current_end,
                    seq_start=len(chunks),
                )
            )
            chunk_start = boundary
            current_end = boundary
            continue
        if boundary - chunk_start > cfg.chunk_size and current_end - chunk_start >= min_chunk:
            chunks.append(_slice_chunk(text, chunk_start, current_end, len(chunks)))
            chunk_start = _aligned_overlap_start(text, current_end, cfg.chunk_overlap, boundaries)
        current_end = boundary
    if current_end > chunk_start:
        chunks.append(_slice_chunk(text, chunk_start, current_end, len(chunks)))
    return chunks


def _split_recursive(text: str, cfg: SplitterConfig) -> List[ParsedChunk]:
    protected = _protected_spans(text)
    units = _build_units(text, protected, list(cfg.separators), cfg.chunk_size)
    return _merge_units(units, cfg.chunk_size, cfg.chunk_overlap)


def _strategy_chain(strategy: str, profile: DocumentProfile | None) -> List[str]:
    if strategy == "heading":
        return ["heading", "legacy"]
    if strategy == "heuristic":
        return ["heuristic", "legacy"]
    if strategy in {"legacy", "recursive"}:
        return ["legacy"]
    profile = profile or DocumentProfile(0, 0, {}, 0, 0, 0, 0, 0)
    chain: List[str] = []
    if profile.heading_total >= 2:
        chain.append("heading")
    if profile.structural_boundary_count >= 2:
        chain.append("heuristic")
    chain.append("legacy")
    return chain


def _chunks_are_valid(chunks: List[ParsedChunk], total_chars: int, chunk_size: int) -> bool:
    if not chunks:
        return False
    if total_chars > chunk_size * 2 and len(chunks) == 1:
        return False
    max_allowed = max(chunk_size * 2, 7500)
    if any(len(chunk.content) > max_allowed for chunk in chunks):
        return False
    if len(chunks) > 20:
        tiny = [chunk for chunk in chunks if len(chunk.content.strip()) < max(60, chunk_size // 8)]
        if len(tiny) / len(chunks) > 0.75:
            return False
    return True


def _protected_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for pattern in PROTECTED_PATTERNS:
        spans.extend(match.span() for match in pattern.finditer(text))
    if not spans:
        return []
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    merged: List[Tuple[int, int]] = []
    for start, end in spans:
        if not merged or start >= merged[-1][1]:
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    return merged


def _build_units(
    text: str,
    protected: List[Tuple[int, int]],
    separators: List[str],
    chunk_size: int,
) -> List[Tuple[str, int, int]]:
    units: List[Tuple[str, int, int]] = []
    pos = 0
    for start, end in protected:
        if start > pos:
            units.extend(_split_region(text[pos:start], pos, separators, chunk_size))
        units.append((text[start:end], start, end))
        pos = end
    if pos < len(text):
        units.extend(_split_region(text[pos:], pos, separators, chunk_size))
    return [unit for unit in units if unit[0]]


def _split_region(text: str, offset: int, separators: List[str], chunk_size: int) -> List[Tuple[str, int, int]]:
    pieces = _split_by_separators(text, separators, chunk_size)
    units: List[Tuple[str, int, int]] = []
    pos = offset
    for piece in pieces:
        units.append((piece, pos, pos + len(piece)))
        pos += len(piece)
    return units


def _split_by_separators(text: str, separators: List[str], chunk_size: int) -> List[str]:
    if not text:
        return []
    if chunk_size > 0 and len(text) <= chunk_size:
        return [text]
    for index, sep in enumerate(separators):
        if not sep or sep not in text:
            continue
        raw = text.split(sep)
        pieces: List[str] = []
        for i, part in enumerate(raw):
            if part:
                pieces.append(part)
            if i < len(raw) - 1:
                pieces.append(sep)
        if len(pieces) <= 1:
            continue
        out: List[str] = []
        remaining = separators[index + 1 :]
        for piece in pieces:
            if chunk_size > 0 and len(piece) > chunk_size and remaining:
                out.extend(_split_by_separators(piece, remaining, chunk_size))
            else:
                out.append(piece)
        return out
    if chunk_size <= 0:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _merge_units(units: List[Tuple[str, int, int]], chunk_size: int, chunk_overlap: int) -> List[ParsedChunk]:
    chunks: List[ParsedChunk] = []
    current: List[Tuple[str, int, int]] = []
    cur_len = 0
    absolute_max = 7500
    for unit in units:
        text, start, end = unit
        unit_len = len(text)
        if unit_len > absolute_max:
            if current:
                chunks.append(_chunk_from_units(current, len(chunks)))
                current = []
                cur_len = 0
            for offset in range(0, unit_len, absolute_max):
                part = text[offset : offset + absolute_max]
                chunks.append(ParsedChunk(part, "", len(chunks), start + offset, start + offset + len(part)))
            continue
        if current and cur_len + unit_len > chunk_size:
            chunks.append(_chunk_from_units(current, len(chunks)))
            current, cur_len = _overlap_units(current, chunk_overlap, chunk_size, unit_len)
        current.append(unit)
        cur_len += unit_len
    if current:
        chunks.append(_chunk_from_units(current, len(chunks)))
    return chunks


def _overlap_units(
    units: List[Tuple[str, int, int]],
    overlap: int,
    chunk_size: int,
    next_len: int,
) -> Tuple[List[Tuple[str, int, int]], int]:
    if overlap <= 0:
        return [], 0
    kept: List[Tuple[str, int, int]] = []
    kept_len = 0
    for unit in reversed(units):
        unit_len = len(unit[0])
        if kept_len + unit_len > overlap or kept_len + unit_len + next_len > chunk_size:
            break
        kept.insert(0, unit)
        kept_len += unit_len
    while kept and not kept[0][0].strip():
        kept_len -= len(kept[0][0])
        kept.pop(0)
    return kept, kept_len


def _chunk_from_units(units: List[Tuple[str, int, int]], seq: int) -> ParsedChunk:
    content = "".join(unit[0] for unit in units)
    return ParsedChunk(content=content, context_header="", seq=seq, start=units[0][1], end=units[-1][2])


def _heading_boundaries(text: str, primary_level: int) -> List[Tuple[int, str]]:
    boundaries: List[Tuple[int, str]] = [(0, "")]
    pos = 0
    in_fence = False
    lines = text.splitlines(keepends=True)
    for line in lines:
        raw = line.rstrip("\r\n")
        if FENCE_RE.match(raw):
            in_fence = not in_fence
        elif not in_fence:
            match = HEADING_RE.match(raw)
            if match:
                level = len(match.group(1))
                if level <= primary_level:
                    if pos == 0:
                        boundaries[0] = (0, raw)
                    else:
                        boundaries.append((pos, raw))
        pos += len(line)
    return boundaries


def _heuristic_boundaries(text: str) -> List[int]:
    boundaries: List[int] = [match.start() for match in re.finditer("\f", text)]
    pos = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        if FENCE_RE.match(raw):
            in_fence = not in_fence
        elif not in_fence and (
            NUMBERED_HEADING_RE.match(raw)
            or ALL_CAPS_HEADING_RE.match(raw)
            or VISUAL_SEPARATOR_RE.match(raw)
        ):
            boundaries.append(pos)
        pos += len(line)
    boundaries.extend(match.end() for match in EXCESSIVE_BLANKS_RE.finditer(text))
    return [boundary for boundary in boundaries if boundary > 0]


def _slice_chunk(text: str, start: int, end: int, seq: int) -> ParsedChunk:
    return ParsedChunk(content=text[start:end], context_header="", seq=seq, start=start, end=end)


def _offset_chunks(chunks: List[ParsedChunk], *, offset: int, seq_start: int) -> List[ParsedChunk]:
    return [
        ParsedChunk(chunk.content, chunk.context_header, seq_start + index, offset + chunk.start, offset + chunk.end)
        for index, chunk in enumerate(chunks)
    ]


def _aligned_overlap_start(text: str, end: int, overlap: int, boundaries: List[int]) -> int:
    if overlap <= 0:
        return end
    lower = max(0, end - overlap)
    candidates = [boundary for boundary in boundaries if lower <= boundary < end]
    if candidates:
        return candidates[-1]
    newline = text.rfind("\n", lower, end)
    return newline + 1 if newline >= lower else lower


def _coalesce_tiny_chunks(chunks: List[ParsedChunk], chunk_size: int) -> List[ParsedChunk]:
    if len(chunks) <= 1:
        return chunks
    target = max(200, chunk_size // 2)
    out: List[ParsedChunk] = []
    current = chunks[0]
    for nxt in chunks[1:]:
        shared = _common_breadcrumb(current.context_header, nxt.context_header)
        if shared and current.end == nxt.start and len(current.content) < target and len(current.content) + len(nxt.content) <= chunk_size:
            current = ParsedChunk(current.content + nxt.content, shared, current.seq, current.start, nxt.end)
            continue
        out.append(current)
        current = nxt
    out.append(current)
    return _resequenced(out)


def _common_breadcrumb(left: str, right: str) -> str:
    if left == right:
        return left
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    common: List[str] = []
    for left_line, right_line in zip(left_lines, right_lines):
        if left_line != right_line:
            break
        common.append(left_line)
    return "\n".join(common)


def _merge_breadcrumbs(parent: str, child: str) -> str:
    if not parent:
        return child
    if not child:
        return parent
    parent_lines = parent.splitlines()
    child_lines = child.splitlines()
    if parent_lines and child_lines and parent_lines[-1].strip() == child_lines[0].strip():
        child_lines = child_lines[1:]
    if not child_lines:
        return parent
    return parent + "\n" + "\n".join(child_lines)


def _resequenced(chunks: List[ParsedChunk]) -> List[ParsedChunk]:
    return [
        ParsedChunk(chunk.content, chunk.context_header, index, chunk.start, chunk.end)
        for index, chunk in enumerate(chunks)
        if chunk.content
    ]


def _ensure_config(cfg: SplitterConfig) -> SplitterConfig:
    chunk_size = cfg.chunk_size or DEFAULT_CHUNK_SIZE
    if cfg.token_limit > 0:
        # Conservative approximation: 4 chars/token with a 10% safety margin.
        chunk_size = min(chunk_size, max(128, int(cfg.token_limit * 4 * 0.9)))
    overlap = max(0, cfg.chunk_overlap if cfg.chunk_overlap is not None else DEFAULT_CHUNK_OVERLAP)
    if overlap > chunk_size // 2:
        overlap = chunk_size // 2
    separators = list(cfg.separators or DEFAULT_SEPARATORS)
    return SplitterConfig(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=separators,
        strategy=cfg.strategy or "legacy",
        token_limit=cfg.token_limit,
        languages=cfg.languages,
    )


def _normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")
