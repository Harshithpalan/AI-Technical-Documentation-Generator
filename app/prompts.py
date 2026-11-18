"""Prompt templates for each documentation type."""

DOC_TYPES = {
    "readme": {
        "label": "README",
        "description": "Project overview, features, installation, usage and configuration.",
        "instructions": (
            "Write a complete, professional README.md for this project. Include: a title and one-paragraph "
            "summary, key features, tech stack, prerequisites, installation, configuration (env vars / settings "
            "found in the code), usage examples with code blocks, project structure, and contributing/license "
            "placeholders. Only describe functionality that actually exists in the code."
        ),
    },
    "api": {
        "label": "API Reference",
        "description": "Every public module, class, function and endpoint with params and returns.",
        "instructions": (
            "Write a detailed API reference. For every public module, class, function, method and HTTP endpoint, "
            "document: signature, purpose, parameters (name, type, description, default), return value, raised "
            "errors/exceptions, and a short usage example. Group by file/module and use consistent headings. "
            "Use Markdown tables for parameters."
        ),
    },
    "architecture": {
        "label": "Architecture Overview",
        "description": "Components, data flow and design decisions, with Mermaid diagrams.",
        "instructions": (
            "Write an architecture overview document. Cover: high-level purpose, main components/modules and their "
            "responsibilities, how they interact, data flow, external dependencies and integrations, key design "
            "decisions and trade-offs, and potential risks or areas for improvement. Include at least one Mermaid "
            "diagram in a ```mermaid code block (flowchart or sequence diagram) that reflects the real code."
        ),
    },
    "docstrings": {
        "label": "Inline Docstrings",
        "description": "Returns the code with idiomatic docstrings/comments added.",
        "instructions": (
            "Return the provided source code with high-quality, idiomatic documentation comments added "
            "(Python docstrings in Google style, JSDoc/TSDoc for JavaScript/TypeScript, Javadoc for Java, "
            "GoDoc for Go, /// for Rust, XML docs for C#, etc.). Do NOT change any logic or formatting other "
            "than adding comments. Output each file in its own fenced code block preceded by a '### <path>' heading."
        ),
    },
    "guide": {
        "label": "Developer Onboarding Guide",
        "description": "Step-by-step guide for new contributors.",
        "instructions": (
            "Write a developer onboarding guide for an engineer joining this project. Include: what the project "
            "does, local setup steps, how to run and test it, a tour of the codebase (where to find what), "
            "coding conventions observed in the code, common tasks (e.g. adding a new endpoint/feature) with "
            "step-by-step instructions, and troubleshooting tips."
        ),
    },
    "custom": {
        "label": "Custom",
        "description": "Your own instructions.",
        "instructions": "",
    },
}

AUDIENCES = {
    "developers": "experienced software developers",
    "beginners": "beginner programmers; explain concepts clearly and avoid unexplained jargon",
    "stakeholders": "non-technical stakeholders; focus on what and why rather than implementation details",
}

DETAIL_LEVELS = {
    "concise": "Be concise: prioritise the most important information.",
    "standard": "Use a balanced level of detail.",
    "comprehensive": "Be comprehensive and thorough; cover edge cases and details.",
}

SYSTEM_PROMPT = (
    "You are an expert technical writer and senior software engineer. You produce accurate, well-structured "
    "technical documentation in GitHub-flavoured Markdown. Never invent features, files, endpoints or "
    "configuration that are not present in the provided source. If something is unclear, say so explicitly."
)


def build_user_prompt(
    doc_type: str,
    code_bundle: str,
    project_name: str = "",
    audience: str = "developers",
    detail: str = "standard",
    custom_instructions: str = "",
    output_language: str = "English",
) -> str:
    spec = DOC_TYPES.get(doc_type, DOC_TYPES["readme"])
    task = spec["instructions"]
    if doc_type == "custom":
        task = custom_instructions.strip() or DOC_TYPES["readme"]["instructions"]
    elif custom_instructions.strip():
        task += f"\n\nAdditional instructions from the user:\n{custom_instructions.strip()}"

    parts = [
        f"## Task\n{task}",
        f"## Audience\nWrite for {AUDIENCES.get(audience, AUDIENCES['developers'])}.",
        f"## Detail level\n{DETAIL_LEVELS.get(detail, DETAIL_LEVELS['standard'])}",
        f"## Output language\nWrite the documentation in {output_language or 'English'}.",
    ]
    if project_name.strip():
        parts.append(f"## Project name\n{project_name.strip()}")
    parts.append(f"## Source code\n{code_bundle}")
    parts.append("Respond with the Markdown document only, without any preamble.")
    return "\n\n".join(parts)
