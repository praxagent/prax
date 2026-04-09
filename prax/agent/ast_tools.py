"""Tree-sitter AST parsing tools for structural code understanding.

Gives spokes (sysadmin, self-improve) the ability to analyze code structure
— classes, functions, imports, dependency graphs — via real AST parsing
rather than text-level grep.  Falls back gracefully when tree-sitter is
not installed.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tree-sitter language registry (lazy-loaded)
# ---------------------------------------------------------------------------

_PARSERS: dict[str, object] = {}

# Extension → language key mapping
_EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

# Language key → file glob patterns
_LANG_GLOBS: dict[str, list[str]] = {
    "python": ["*.py"],
    "javascript": ["*.js", "*.jsx", "*.mjs", "*.cjs"],
    "typescript": ["*.ts", "*.tsx"],
}

_MAX_FILES = 50
_MAX_OUTPUT_LINES = 200


def _get_parser(lang: str):
    """Return a configured tree-sitter Parser for *lang*, or None."""
    if lang in _PARSERS:
        return _PARSERS[lang]

    try:
        from tree_sitter import Language, Parser

        if lang == "python":
            import tree_sitter_python as tsp
            language = Language(tsp.language())
        elif lang == "javascript":
            import tree_sitter_javascript as tsjs
            language = Language(tsjs.language())
        elif lang == "typescript":
            import tree_sitter_typescript as tsts
            language = Language(tsts.language_typescript())
        elif lang == "tsx":
            import tree_sitter_typescript as tsts
            language = Language(tsts.language_tsx())
        else:
            _PARSERS[lang] = None
            return None

        parser = Parser(language)
        _PARSERS[lang] = parser
        return parser
    except ImportError:
        _PARSERS[lang] = None
        return None


def _detect_language(file_path: str) -> str | None:
    """Detect language key from file extension."""
    ext = Path(file_path).suffix.lower()
    return _EXT_MAP.get(ext)


def _resolve_path(raw_path: str) -> str:
    """Resolve workspace/source paths to absolute paths."""
    p = Path(raw_path)
    if p.is_absolute():
        return str(p)
    # Try /source/ first (self-improve sandbox), then /app, then cwd
    for base in ["/source", "/app"]:
        candidate = Path(base) / raw_path
        if candidate.exists():
            return str(candidate)
    return str(Path.cwd() / raw_path)


# ---------------------------------------------------------------------------
# Python-specific extraction helpers
# ---------------------------------------------------------------------------

def _extract_python_structures(root_node, source_bytes: bytes) -> dict:
    """Extract classes, functions, and imports from a Python AST."""
    result: dict = {
        "classes": [],
        "functions": [],
        "imports": [],
    }

    for child in root_node.children:
        if child.type == "class_definition":
            result["classes"].append(_extract_python_class(child, source_bytes))
        elif child.type == "function_definition":
            result["functions"].append(
                _extract_python_function(child, source_bytes)
            )
        elif child.type == "decorated_definition":
            # A decorated class or function
            inner = None
            decorators = []
            for sub in child.children:
                if sub.type == "decorator":
                    decorators.append(
                        source_bytes[sub.start_byte:sub.end_byte]
                        .decode("utf-8", errors="replace")
                        .strip()
                    )
                elif sub.type == "class_definition":
                    inner = _extract_python_class(sub, source_bytes)
                    inner["decorators"] = decorators
                elif sub.type == "function_definition":
                    inner = _extract_python_function(sub, source_bytes)
                    inner["decorators"] = decorators
            if inner:
                kind = "classes" if "methods" in inner else "functions"
                result[kind].append(inner)
        elif child.type in ("import_statement", "import_from_statement"):
            result["imports"].append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace")
                .strip()
            )

    return result


def _extract_python_class(node, source_bytes: bytes) -> dict:
    """Extract class name, bases, and methods."""
    info: dict = {
        "name": "",
        "bases": [],
        "methods": [],
        "line": node.start_point[0] + 1,
        "decorators": [],
    }

    for child in node.children:
        if child.type == "identifier":
            info["name"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "argument_list":
            for arg in child.children:
                if arg.type == "identifier":
                    info["bases"].append(
                        arg.text.decode("utf-8", errors="replace")
                    )
                elif arg.type == "keyword_argument":
                    info["bases"].append(
                        arg.text.decode("utf-8", errors="replace")
                    )
        elif child.type == "block":
            for stmt in child.children:
                if stmt.type == "function_definition":
                    info["methods"].append(
                        _extract_python_function(stmt, source_bytes)
                    )
                elif stmt.type == "decorated_definition":
                    decorators = []
                    for sub in stmt.children:
                        if sub.type == "decorator":
                            decorators.append(
                                source_bytes[sub.start_byte:sub.end_byte]
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                        elif sub.type == "function_definition":
                            fn = _extract_python_function(sub, source_bytes)
                            fn["decorators"] = decorators
                            info["methods"].append(fn)

    return info


def _extract_python_function(node, source_bytes: bytes) -> dict:
    """Extract function name, parameters, return type, decorators."""
    info: dict = {
        "name": "",
        "params": [],
        "return_type": None,
        "line": node.start_point[0] + 1,
        "decorators": [],
    }

    for child in node.children:
        if child.type == "identifier":
            info["name"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "parameters":
            for param in child.children:
                if param.type in (
                    "identifier",
                    "typed_parameter",
                    "default_parameter",
                    "typed_default_parameter",
                    "list_splat_pattern",
                    "dictionary_splat_pattern",
                ):
                    info["params"].append(
                        source_bytes[param.start_byte:param.end_byte]
                        .decode("utf-8", errors="replace")
                    )
        elif child.type == "type":
            info["return_type"] = (
                child.text.decode("utf-8", errors="replace")
            )

    return info


# ---------------------------------------------------------------------------
# JS/TS extraction helpers
# ---------------------------------------------------------------------------

def _extract_js_structures(root_node, source_bytes: bytes) -> dict:
    """Extract classes, functions, and imports from JS/TS AST."""
    result: dict = {
        "classes": [],
        "functions": [],
        "imports": [],
    }

    def _walk(node):
        if node.type in ("import_statement", "import"):
            result["imports"].append(
                source_bytes[node.start_byte:node.end_byte]
                .decode("utf-8", errors="replace")
                .strip()
            )
        elif node.type in ("function_declaration", "function"):
            result["functions"].append(_extract_js_function(node, source_bytes))
        elif node.type == "class_declaration":
            result["classes"].append(_extract_js_class(node, source_bytes))
        elif node.type == "export_statement":
            # Recurse into exported declarations
            for child in node.children:
                _walk(child)
        elif node.type == "lexical_declaration":
            # const foo = () => {} or const foo = function() {}
            for child in node.children:
                if child.type == "variable_declarator":
                    _maybe_extract_arrow_fn(child, source_bytes, result)
        else:
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "class_declaration",
                    "import_statement",
                    "export_statement",
                    "lexical_declaration",
                ):
                    _walk(child)

    for child in root_node.children:
        _walk(child)

    return result


def _maybe_extract_arrow_fn(node, source_bytes: bytes, result: dict):
    """Extract arrow functions assigned to const variables."""
    name = ""
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8", errors="replace")
        elif child.type == "arrow_function":
            fn_info = {
                "name": name,
                "params": [],
                "return_type": None,
                "line": node.start_point[0] + 1,
                "decorators": [],
            }
            for sub in child.children:
                if sub.type == "formal_parameters":
                    for p in sub.children:
                        if p.type in ("identifier", "required_parameter",
                                      "optional_parameter"):
                            fn_info["params"].append(
                                source_bytes[p.start_byte:p.end_byte]
                                .decode("utf-8", errors="replace")
                            )
            result["functions"].append(fn_info)


def _extract_js_function(node, source_bytes: bytes) -> dict:
    """Extract JS/TS function info."""
    info: dict = {
        "name": "",
        "params": [],
        "return_type": None,
        "line": node.start_point[0] + 1,
        "decorators": [],
    }
    for child in node.children:
        if child.type == "identifier":
            info["name"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "formal_parameters":
            for param in child.children:
                if param.type in ("identifier", "required_parameter",
                                  "optional_parameter", "rest_parameter"):
                    info["params"].append(
                        source_bytes[param.start_byte:param.end_byte]
                        .decode("utf-8", errors="replace")
                    )
        elif child.type == "type_annotation":
            info["return_type"] = (
                child.text.decode("utf-8", errors="replace")
            )
    return info


def _extract_js_class(node, source_bytes: bytes) -> dict:
    """Extract JS/TS class info."""
    info: dict = {
        "name": "",
        "bases": [],
        "methods": [],
        "line": node.start_point[0] + 1,
        "decorators": [],
    }
    for child in node.children:
        if child.type in ("identifier", "type_identifier") and not info["name"]:
            info["name"] = child.text.decode("utf-8", errors="replace")
        elif child.type == "class_heritage":
            for sub in child.children:
                if sub.type in ("identifier", "type_identifier"):
                    info["bases"].append(
                        sub.text.decode("utf-8", errors="replace")
                    )
        elif child.type == "class_body":
            for member in child.children:
                if member.type in ("method_definition", "public_field_definition"):
                    method_info = {
                        "name": "",
                        "params": [],
                        "return_type": None,
                        "line": member.start_point[0] + 1,
                        "decorators": [],
                    }
                    for sub in member.children:
                        if sub.type in ("property_identifier", "identifier"):
                            method_info["name"] = (
                                sub.text.decode("utf-8", errors="replace")
                            )
                        elif sub.type == "formal_parameters":
                            for p in sub.children:
                                if p.type in (
                                    "identifier",
                                    "required_parameter",
                                    "optional_parameter",
                                ):
                                    method_info["params"].append(
                                        source_bytes[p.start_byte:p.end_byte]
                                        .decode("utf-8", errors="replace")
                                    )
                    if method_info["name"]:
                        info["methods"].append(method_info)
    return info


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_structure(data: dict, file_path: str, line_count: int) -> str:
    """Format extracted structure as readable text."""
    lines = [f"## {file_path}  ({line_count} lines)\n"]

    # Imports
    if data["imports"]:
        lines.append("### Imports")
        for imp in data["imports"]:
            lines.append(f"  {imp}")
        lines.append("")

    # Classes
    for cls in data["classes"]:
        deco_str = " ".join(cls.get("decorators", []))
        base_str = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
        prefix = f"{deco_str} " if deco_str else ""
        lines.append(
            f"### {prefix}class {cls['name']}{base_str}  (line {cls['line']})"
        )
        for method in cls["methods"]:
            m_deco = " ".join(method.get("decorators", []))
            m_prefix = f"{m_deco} " if m_deco else ""
            params = ", ".join(method["params"])
            ret = f" -> {method['return_type']}" if method.get("return_type") else ""
            lines.append(
                f"  {m_prefix}def {method['name']}({params}){ret}  "
                f"(line {method['line']})"
            )
        lines.append("")

    # Top-level functions
    if data["functions"]:
        lines.append("### Functions")
        for fn in data["functions"]:
            deco_str = " ".join(fn.get("decorators", []))
            prefix = f"{deco_str} " if deco_str else ""
            params = ", ".join(fn["params"])
            ret = f" -> {fn['return_type']}" if fn.get("return_type") else ""
            lines.append(
                f"  {prefix}def {fn['name']}({params}){ret}  "
                f"(line {fn['line']})"
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 1: code_structure
# ---------------------------------------------------------------------------

@tool
def code_structure(file_path: str) -> str:
    """Analyze the structure of a source code file using AST parsing.

    Returns classes, functions, imports, and their relationships without
    reading the entire file content. Much more efficient than reading
    large files when you just need to understand the structure.

    Args:
        file_path: Path to the source file (relative to /app or /source in
                   Docker, or absolute path locally).

    Returns:
        Structured summary including classes with their methods and base
        classes, top-level functions with parameters, imports, and
        approximate line count.
    """
    resolved = _resolve_path(file_path)

    if not os.path.isfile(resolved):
        return f"Error: file not found: {resolved}"

    lang = _detect_language(resolved)
    if lang is None:
        ext = Path(resolved).suffix
        return (
            f"Error: unsupported file extension '{ext}'. "
            f"Supported: {', '.join(sorted(_EXT_MAP.keys()))}"
        )

    parser = _get_parser(lang)
    if parser is None:
        return (
            f"Error: tree-sitter parser for '{lang}' is not available. "
            f"Install tree-sitter-{lang}."
        )

    try:
        source = Path(resolved).read_bytes()
    except OSError as exc:
        return f"Error reading file: {exc}"

    tree = parser.parse(source)
    line_count = source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)

    if lang == "python":
        data = _extract_python_structures(tree.root_node, source)
    else:
        data = _extract_js_structures(tree.root_node, source)

    return _format_structure(data, file_path, line_count)


# ---------------------------------------------------------------------------
# Tool 2: code_dependencies
# ---------------------------------------------------------------------------

@tool
def code_dependencies(directory: str, language: str = "python") -> str:
    """Map import dependencies across files in a directory.

    Builds a dependency graph showing which files import from which other
    files.  Useful for understanding module structure and finding circular
    imports.

    Args:
        directory: Directory to scan (scans recursively).
        language: Programming language ("python", "javascript", "typescript").

    Returns:
        Dependency map showing file imports, potential circular imports,
        and most-imported modules (hub files).
    """
    resolved = _resolve_path(directory)

    if not os.path.isdir(resolved):
        return f"Error: directory not found: {resolved}"

    globs = _LANG_GLOBS.get(language, [])
    if not globs:
        return (
            f"Error: unsupported language '{language}'. "
            f"Supported: {', '.join(sorted(_LANG_GLOBS.keys()))}"
        )

    # Collect matching files
    files: list[str] = []
    for root, _dirs, filenames in os.walk(resolved):
        for fname in filenames:
            if any(fnmatch.fnmatch(fname, g) for g in globs):
                files.append(os.path.join(root, fname))
        if len(files) >= _MAX_FILES:
            break

    files = sorted(files)[:_MAX_FILES]

    if not files:
        return f"No {language} files found in {directory}"

    # Extract imports from each file
    adjacency: dict[str, list[str]] = {}
    import_counts: dict[str, int] = {}

    for fpath in files:
        rel = os.path.relpath(fpath, resolved)
        lang_key = _detect_language(fpath)
        if lang_key is None:
            continue
        parser = _get_parser(lang_key)
        if parser is None:
            continue

        try:
            source = Path(fpath).read_bytes()
        except OSError:
            continue

        tree = parser.parse(source)
        if lang_key == "python":
            data = _extract_python_structures(tree.root_node, source)
        else:
            data = _extract_js_structures(tree.root_node, source)

        imports = data.get("imports", [])
        adjacency[rel] = imports

        # Count which modules are imported most
        for imp in imports:
            # Extract the module name from the import statement
            module = _extract_module_name(imp, language)
            if module:
                import_counts[module] = import_counts.get(module, 0) + 1

    # Detect circular imports (simple cycle detection)
    cycles = _detect_cycles(adjacency, language, resolved)

    # Format output
    lines = [f"## Dependency Map: {directory}  ({len(files)} files)\n"]

    # File → imports
    lines.append("### File Imports")
    for rel_path, imports in sorted(adjacency.items()):
        if imports:
            lines.append(f"  **{rel_path}**")
            for imp in imports[:10]:  # Cap per-file imports shown
                lines.append(f"    {imp}")
            if len(imports) > 10:
                lines.append(f"    ... and {len(imports) - 10} more")
    lines.append("")

    # Circular imports
    if cycles:
        lines.append("### Potential Circular Imports")
        for cycle in cycles[:10]:
            lines.append(f"  {' -> '.join(cycle)}")
        lines.append("")
    else:
        lines.append("### Circular Imports: None detected\n")

    # Hub files
    if import_counts:
        lines.append("### Most-Imported Modules (hub files)")
        top = sorted(import_counts.items(), key=lambda x: -x[1])[:15]
        for module, count in top:
            lines.append(f"  {module}: {count} imports")
        lines.append("")

    output = "\n".join(lines)
    # Truncate if too long
    output_lines = output.split("\n")
    if len(output_lines) > _MAX_OUTPUT_LINES:
        output = "\n".join(output_lines[:_MAX_OUTPUT_LINES])
        output += f"\n\n... truncated ({len(output_lines)} total lines)"

    return output


def _extract_module_name(import_stmt: str, language: str) -> str | None:
    """Extract the module name from an import statement string."""
    import_stmt = import_stmt.strip()
    if language == "python":
        if import_stmt.startswith("from "):
            # "from foo.bar import baz" → "foo.bar"
            parts = import_stmt.split()
            if len(parts) >= 2:
                return parts[1]
        elif import_stmt.startswith("import "):
            parts = import_stmt.split()
            if len(parts) >= 2:
                return parts[1].rstrip(",")
    else:
        # JS/TS: import ... from "module"
        if "from" in import_stmt:
            # Extract the string after 'from'
            idx = import_stmt.rfind("from")
            rest = import_stmt[idx + 4:].strip().strip("'\"`;")
            if rest:
                return rest
    return None


def _detect_cycles(
    adjacency: dict[str, list[str]],
    language: str,
    base_dir: str,
) -> list[list[str]]:
    """Simple DFS cycle detection on the import graph."""
    # Build a normalized adjacency list: file → set of imported file paths
    norm: dict[str, set[str]] = {}
    all_files = set(adjacency.keys())

    for rel_path, imports in adjacency.items():
        targets: set[str] = set()
        for imp in imports:
            module = _extract_module_name(imp, language)
            if module is None:
                continue
            if language == "python":
                # Convert dotted module to file path
                candidate = module.replace(".", "/") + ".py"
                # Also try as package __init__
                candidate2 = module.replace(".", "/") + "/__init__.py"
                if candidate in all_files:
                    targets.add(candidate)
                elif candidate2 in all_files:
                    targets.add(candidate2)
            else:
                # JS/TS: relative imports
                if module.startswith("."):
                    from_dir = os.path.dirname(rel_path)
                    resolved = os.path.normpath(os.path.join(from_dir, module))
                    for ext in (".js", ".ts", ".tsx", ".jsx"):
                        candidate = resolved + ext
                        if candidate in all_files:
                            targets.add(candidate)
                            break
                    # Also try as-is
                    if resolved in all_files:
                        targets.add(resolved)

        norm[rel_path] = targets

    # DFS
    cycles: list[list[str]] = []
    visited: set[str] = set()
    path: list[str] = []
    on_stack: set[str] = set()

    def _dfs(node: str):
        if len(cycles) >= 10:
            return
        visited.add(node)
        on_stack.add(node)
        path.append(node)

        for neighbor in norm.get(node, set()):
            if neighbor in on_stack:
                # Found a cycle
                idx = path.index(neighbor)
                cycles.append(path[idx:] + [neighbor])
            elif neighbor not in visited:
                _dfs(neighbor)

        path.pop()
        on_stack.discard(node)

    for node in sorted(norm.keys()):
        if node not in visited:
            _dfs(node)

    return cycles


# ---------------------------------------------------------------------------
# Tool 3: code_search_ast
# ---------------------------------------------------------------------------

@tool
def code_search_ast(directory: str, pattern: str, kind: str = "function") -> str:
    """Search for code structures by name across a codebase using AST.

    Unlike text grep, this understands code structure — searching for a
    function named "build" won't match variable names or comments
    containing "build".

    Args:
        directory: Directory to search.
        pattern: Name pattern to search for (supports * wildcards).
        kind: What to search for: "function", "class", "method", or "import".

    Returns:
        List of matches with file path, line number, and context.
    """
    resolved = _resolve_path(directory)

    if not os.path.isdir(resolved):
        return f"Error: directory not found: {resolved}"

    valid_kinds = ("function", "class", "method", "import")
    if kind not in valid_kinds:
        return f"Error: kind must be one of {valid_kinds}, got '{kind}'"

    # Collect all supported files
    files: list[str] = []
    for root, _dirs, filenames in os.walk(resolved):
        for fname in filenames:
            if _detect_language(fname) is not None:
                files.append(os.path.join(root, fname))
        if len(files) >= _MAX_FILES:
            break

    files = sorted(files)[:_MAX_FILES]

    if not files:
        return f"No supported source files found in {directory}"

    matches: list[str] = []

    for fpath in files:
        rel = os.path.relpath(fpath, resolved)
        lang_key = _detect_language(fpath)
        if lang_key is None:
            continue

        parser = _get_parser(lang_key)
        if parser is None:
            continue

        try:
            source = Path(fpath).read_bytes()
        except OSError:
            continue

        tree = parser.parse(source)

        if lang_key == "python":
            data = _extract_python_structures(tree.root_node, source)
        else:
            data = _extract_js_structures(tree.root_node, source)

        if kind == "function":
            for fn in data["functions"]:
                if fnmatch.fnmatch(fn["name"], pattern):
                    params = ", ".join(fn["params"])
                    ret = f" -> {fn['return_type']}" if fn.get("return_type") else ""
                    decos = " ".join(fn.get("decorators", []))
                    prefix = f"{decos} " if decos else ""
                    matches.append(
                        f"  {rel}:{fn['line']}  "
                        f"{prefix}def {fn['name']}({params}){ret}"
                    )

        elif kind == "class":
            for cls in data["classes"]:
                if fnmatch.fnmatch(cls["name"], pattern):
                    bases = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
                    n_methods = len(cls["methods"])
                    matches.append(
                        f"  {rel}:{cls['line']}  "
                        f"class {cls['name']}{bases}  ({n_methods} methods)"
                    )

        elif kind == "method":
            for cls in data["classes"]:
                for method in cls["methods"]:
                    if fnmatch.fnmatch(method["name"], pattern):
                        params = ", ".join(method["params"])
                        matches.append(
                            f"  {rel}:{method['line']}  "
                            f"{cls['name']}.{method['name']}({params})"
                        )

        elif kind == "import":
            for imp in data["imports"]:
                if fnmatch.fnmatch(imp, f"*{pattern}*"):
                    matches.append(f"  {rel}: {imp}")

    if not matches:
        return f"No {kind} matching '{pattern}' found in {directory}"

    header = (
        f"## AST Search: {kind} matching '{pattern}' in {directory}\n"
        f"Found {len(matches)} match(es):\n"
    )
    return header + "\n".join(matches[:_MAX_OUTPUT_LINES])


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_ast_tools() -> list:
    """Return AST analysis tools.  Empty list if tree-sitter not installed."""
    try:
        import tree_sitter  # noqa: F401
        return [code_structure, code_dependencies, code_search_ast]
    except ImportError:
        logger.info("tree-sitter not installed — AST tools disabled")
        return []
