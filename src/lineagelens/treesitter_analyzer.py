"""Universal Tree-sitter AST parser engine for LineageLens."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .model import CodeGraph, Container, Evidence, Relation, Symbol

logger = logging.getLogger(__name__)

# File extension to language mapping
EXTENSION_LANG_MAP = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".cs": "c_sharp",
}


def _get_tree_sitter_parser(language_name: str) -> Any | None:
    """Attempt to load a Tree-sitter parser for the given language name."""
    try:
        from tree_sitter import Language, Parser
    except ImportError:
        logger.warning(
            "tree-sitter is not installed. Install with `pip install 'lineagelens[treesitter]'`."
        )
        return None

    try:
        # Try loading official language bindings e.g. tree_sitter_python
        module_name = f"tree_sitter_{language_name}"
        import importlib
        lang_module = importlib.import_module(module_name)
        lang_capsule = getattr(lang_module, "language", None)
        if callable(lang_capsule):
            ts_lang = Language(lang_capsule())
            parser = Parser(ts_lang)
            return parser
    except (ImportError, AttributeError, Exception) as exc:
        logger.debug(f"Could not load tree-sitter bindings for {language_name}: {exc}")

    return None


class TreeSitterAnalyzer:
    """Universal Tree-sitter AST analyzer producing LineageLens CodeGraph."""

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.parsers: dict[str, Any] = {}

    def analyze_project(self, project_root: Path) -> CodeGraph:
        """Scan project source roots using Tree-sitter."""
        graph = CodeGraph(project_root=str(project_root))

        source_roots = [project_root / root for root in self.config.source_roots]
        files_to_scan: list[tuple[Path, str]] = []

        for root in source_roots:
            if not root.exists():
                continue
            for ext, lang in EXTENSION_LANG_MAP.items():
                for path in root.glob(f"**/*{ext}"):
                    if path.is_file() and not self._is_excluded(path):
                        files_to_scan.append((path, lang))

        # Also check root level if no files found in source_roots
        if not files_to_scan:
            for ext, lang in EXTENSION_LANG_MAP.items():
                for path in project_root.glob(f"**/*{ext}"):
                    if path.is_file() and not self._is_excluded(path):
                        files_to_scan.append((path, lang))

        # Process scanned files
        for file_path, lang in files_to_scan:
            rel_file = str(file_path.relative_to(project_root))
            self._analyze_file(file_path, rel_file, lang, graph)

        # Build containers for modules
        self._build_containers(graph)

        # Resolve simple name-matching call relations
        self._resolve_call_relations(graph)

        return graph

    def _is_excluded(self, path: Path) -> bool:
        parts = path.parts
        return any(
            ignored in parts
            for ignored in (".git", ".lineagelens", "node_modules", ".venv", "build", "dist", "target", "coverage")
        )

    def _analyze_file(self, file_path: Path, rel_file: str, lang: str, graph: CodeGraph) -> None:
        try:
            content_bytes = file_path.read_bytes()
        except Exception as exc:
            logger.warning(f"Could not read {file_path}: {exc}")
            return

        parser = self.parsers.get(lang)
        if parser is None and lang not in self.parsers:
            parser = _get_tree_sitter_parser(lang)
            self.parsers[lang] = parser

        if parser is not None:
            self._parse_with_tree_sitter(parser, content_bytes, rel_file, lang, graph)
        else:
            # Fallback simple line scanner if tree-sitter binding isn't available
            self._fallback_line_scan(content_bytes, rel_file, lang, graph)

    def _parse_with_tree_sitter(
        self, parser: Any, content: bytes, rel_file: str, lang: str, graph: CodeGraph
    ) -> None:
        tree = parser.parse(content)
        root_node = tree.root_node
        module_name = rel_file.replace("/", ".").rsplit(".", 1)[0]

        # Traverse AST nodes
        self._walk_node(root_node, content, rel_file, module_name, parent_symbol_id=None, graph=graph)

    def _walk_node(
        self,
        node: Any,
        content: bytes,
        rel_file: str,
        module_name: str,
        parent_symbol_id: str | None,
        graph: CodeGraph,
    ) -> None:
        node_type = node.type
        symbol_created_id = parent_symbol_id

        if node_type in ("function_definition", "method_declaration", "function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode("utf-8") if name_node else "unknown_func"
            kind = "method" if parent_symbol_id else "function"
            symbol_id = f"{rel_file}::{name}" if not parent_symbol_id else f"{parent_symbol_id}.{name}"
            
            symbol = Symbol(
                id=symbol_id,
                kind=kind,
                name=name,
                file=rel_file,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                module=module_name,
                parent=parent_symbol_id or module_name,
            )
            graph.add_symbol(symbol)
            symbol_created_id = symbol_id

        elif node_type in ("class_definition", "class_declaration"):
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode("utf-8") if name_node else "unknown_class"
            symbol_id = f"{rel_file}::{name}"
            
            symbol = Symbol(
                id=symbol_id,
                kind="class",
                name=name,
                file=rel_file,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                module=module_name,
                parent=module_name,
            )
            graph.add_symbol(symbol)
            symbol_created_id = symbol_id

        elif node_type in ("call_expression", "method_invocation", "call", "jsx_element", "jsx_self_closing_element"):
            func_node = None
            if node_type == "jsx_self_closing_element":
                func_node = node.child_by_field_name("name")
            elif node_type == "jsx_element":
                opening = node.child_by_field_name("open_tag") or (node.children[0] if node.children else None)
                if opening:
                    func_node = opening.child_by_field_name("name") or (opening.children[1] if len(opening.children) > 1 else None)
            else:
                func_node = node.child_by_field_name("function") or node.child_by_field_name("name") or (node.children[0] if node.children else None)

            if func_node and parent_symbol_id:
                call_target_name = func_node.text.decode("utf-8").split(".")[-1]
                if call_target_name and (node_type not in ("jsx_element", "jsx_self_closing_element") or call_target_name[0].isupper()):
                    graph.add_relation(Relation(
                        source=parent_symbol_id,
                        target=call_target_name,
                        kind="CALLS",
                        file=rel_file,
                        line=node.start_point[0] + 1,
                        evidence=Evidence(tier="deterministic_fact", label="static_ast"),
                        resolution="unresolved_dynamic_dispatch",
                        resolution_evidence=Evidence(tier="deterministic_heuristic", label="tree_sitter_ast"),
                    ))

        for child in node.children:
            self._walk_node(child, content, rel_file, module_name, symbol_created_id, graph)

    def _fallback_line_scan(
        self, content: bytes, rel_file: str, lang: str, graph: CodeGraph
    ) -> None:
        """Fallback lightweight Regex/line scanner if language tree-sitter binary is missing."""
        import re

        lines = content.decode("utf-8", errors="replace").splitlines()
        module_name = rel_file.replace("/", ".").rsplit(".", 1)[0]
        
        current_class: str | None = None
        current_func: str | None = None

        for idx, line in enumerate(lines, 1):
            line_str = line.strip()
            clean_line = re.sub(r"^(export\s+|default\s+|async\s+|public\s+|private\s+|protected\s+)+", "", line_str)

            # Class definitions
            if "class " in clean_line:
                parts = clean_line.split()
                if "class" in parts:
                    class_idx = parts.index("class")
                    if class_idx + 1 < len(parts):
                        cname = parts[class_idx + 1].split("(")[0].split("{")[0].rstrip(":")
                        if cname:
                            current_class = cname
                            symbol_id = f"{rel_file}::{cname}"
                            graph.add_symbol(Symbol(
                                id=symbol_id,
                                kind="class",
                                name=cname,
                                file=rel_file,
                                line=idx,
                                module=module_name,
                                parent=module_name,
                            ))

            # Function / Method / Component definitions
            elif clean_line.startswith(("def ", "function ")) or (clean_line.startswith("const ") and ("=>" in clean_line or "function" in clean_line)):
                tokens = clean_line.split("(")
                if tokens:
                    first_part = tokens[0].split()
                    name = first_part[-1] if first_part else "unknown"
                    name = name.rstrip(":").rstrip("=").strip()
                    if name and name not in ("if", "for", "while", "class", "{", "return"):
                        kind = "method" if current_class else "function"
                        symbol_id = f"{rel_file}::{name}" if not current_class else f"{rel_file}::{current_class}.{name}"
                        current_func = symbol_id
                        graph.add_symbol(Symbol(
                            id=symbol_id,
                            kind=kind,
                            name=name,
                            file=rel_file,
                            line=idx,
                            module=module_name,
                            parent=current_class or module_name,
                        ))

            # JSX element call site detection e.g. <HUDPanels ... />
            jsx_matches = re.findall(r"<([A-Z]\w+)", line_str)
            for target_comp in jsx_matches:
                caller = current_func or module_name
                graph.add_relation(Relation(
                    source=caller,
                    target=target_comp,
                    kind="CALLS",
                    file=rel_file,
                    line=idx,
                    evidence=Evidence(tier="deterministic_fact", label="static_ast"),
                    resolution="unresolved_dynamic_dispatch",
                    resolution_evidence=Evidence(tier="deterministic_heuristic", label="fallback_scanner"),
                ))



    def _build_containers(self, graph: CodeGraph) -> None:
        modules: set[str] = set()
        for sym in graph.symbols.values():
            if sym.module:
                modules.add(sym.module)
                
        for mod in modules:
            parts = mod.split(".")
            for i in range(1, len(parts) + 1):
                cid = ".".join(parts[:i])
                if cid not in graph.containers:
                    kind = "package" if i < len(parts) else "module"
                    parent_id = ".".join(parts[:i-1]) if i > 1 else None
                    graph.add_container(Container(
                        id=cid,
                        kind=kind,  # type: ignore[arg-type]
                        name=parts[i-1],
                        file=None,
                        parent=parent_id,
                    ))

    def _resolve_call_relations(self, graph: CodeGraph) -> None:
        """Resolve target names in relations against actual graph symbol IDs."""
        symbol_name_map: dict[str, list[str]] = {}
        for sym_id, sym in graph.symbols.items():
            symbol_name_map.setdefault(sym.name, []).append(sym_id)

        resolved_relations: list[Relation] = []
        for rel in graph.relations:
            if rel.target in graph.symbols:
                resolved_relations.append(rel)
            elif rel.target in symbol_name_map:
                possible_targets = symbol_name_map[rel.target]
                # Pick target in same file if possible
                same_file_targets = [t for t in possible_targets if t.startswith(rel.file)]
                chosen_target = same_file_targets[0] if same_file_targets else possible_targets[0]
                
                resolved_relations.append(Relation(
                    source=rel.source,
                    target=chosen_target,
                    kind=rel.kind,
                    file=rel.file,
                    line=rel.line,
                    evidence=Evidence(tier="deterministic_fact", label="static_ast"),
                    resolution="resolved",
                    resolution_evidence=Evidence(tier="deterministic_heuristic", label="tree_sitter_name_match"),
                ))

        graph.relations = resolved_relations
