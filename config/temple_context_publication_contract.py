"""Pure R6D publication-contract fingerprint constructor.

A fingerprint is not review provenance or publication authorization.
Runtime contract versions must be resolved and approved separately.
"""
from dataclasses import asdict
from hashlib import sha256
import json

from config.temple_momentum_policy import TEMPLE_MOMENTUM_POLICY

_SCHEMA = "r6d-sr1-publication-contract-v1"


def _required(label, value):
    if (type(value) is not str or not value
            or value != value.strip() or len(value) > 160
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError("R6D invalid contract value: " + label)
    return value


def fingerprint_temple_context_contract(
    *, sanitizer_model, deidentification_version, aggregation_version,
    embedding_model, extraction_contract_version,
    privacy_review_contract_version, policy,
):
    if (type(policy) is not type(TEMPLE_MOMENTUM_POLICY)
            or policy != TEMPLE_MOMENTUM_POLICY
            or policy.version != "r6d-wave-v2"
            or policy.live_oracle_enabled is not False
            or policy.automatic_scheduling_enabled is not False):
        raise ValueError("R6D noncanonical Momentum policy")

    review = {
        "sanitizer_model": _required(
            "sanitizer_model", sanitizer_model),
        "deidentification_version": _required(
            "deidentification_version", deidentification_version),
        "aggregation_version": _required(
            "aggregation_version", aggregation_version),
        "embedding_model": _required(
            "embedding_model", embedding_model),
        "extraction_contract_version": _required(
            "extraction_contract_version", extraction_contract_version),
        "privacy_review_contract_version": _required(
            "privacy_review_contract_version",
            privacy_review_contract_version),
    }

    document = {
        "schema": _SCHEMA,
        "momentum_policy": asdict(TEMPLE_MOMENTUM_POLICY),
        "review_contract": review,
    }

    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")

    return sha256(encoded).hexdigest()

import ast as _ast
import os as _os
from pathlib import Path as _Path

_APPROVED_FUNCTIONS = {
    "extract_temple_context_theme":
        "b70ce2883bde2323fae6d129fe2b672a4f7b61c987329ff0c013fe5f620b3853",
    "_validate_temple_context_extract_payload":
        "dad288f61bfdcbc3fe7b5f76f7250a6877c8418acf6a36f9d9b7110298c8d928",
    "_validate_temple_context_review_payload":
        "d43746b0b171650447a8353197b4fc328b030629c5b37073eab6dbdb46bb81ab",
    "_corpus_identifier_flags":
        "32498d6936b97cae6feffdf6d1af2a6459daedab4af11e5e830dd958dbe9f438",
    "_execute_temple_context_openai":
        "d630ff04c5cc57f2752738369c9a96f1f2af9f406f4f67234e90fda29c78afbe",
    "_parse_corpus_sanitizer_json":
        "9ce7023927b763e539e01545c18b107510fd6cfdf4dfc0a0a6b7684fcae776a2",
    "get_openai_client":
        "1b8380cd4f6160e0280baf63c5b794a2a9c83b105c5a276cd47dfafd92d94160",
    "generate_text_embedding":
        "1f230fdc3d5514b460496cd046c55bb8d5845b6147cce7a7fbe3c5e6cc5fa7d3",
}

_APPROVED_PROMPTS = {
    "extraction_system":
        "d094be3c960519825645001800eece523d320dafbc88714166c00719b356b494",
    "review_system":
        "6d9017138453e401d48f33574661d054b4268d0a0fa3d3420a7a0e806f2e1234",
}


def _contract_hash(document):
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _verify_review_source(tree):
    functions = {}
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            if node.name in functions:
                raise ValueError("R6D duplicate source function")
            functions[node.name] = node

    for name, expected in _APPROVED_FUNCTIONS.items():
        node = functions.get(name)
        if node is None:
            raise ValueError("R6D approved function missing: " + name)
        actual = sha256(
            _ast.dump(
                node, annotate_fields=True,
                include_attributes=False,
            ).encode("utf-8")
        ).hexdigest()
        if actual != expected:
            raise ValueError("R6D review implementation changed: " + name)

    extraction = functions["extract_temple_context_theme"]
    for name, expected in _APPROVED_PROMPTS.items():
        matches = [
            node for node in _ast.walk(extraction)
            if isinstance(node, _ast.Assign)
            and any(
                isinstance(target, _ast.Name) and target.id == name
                for target in node.targets
            )
        ]
        if len(matches) != 1:
            raise ValueError("R6D prompt assignment changed")

        expr = matches[0].value
        if not (
            isinstance(expr, _ast.Call)
            and isinstance(expr.func, _ast.Attribute)
            and expr.func.attr == "strip"
            and isinstance(expr.func.value, _ast.Constant)
            and type(expr.func.value.value) is str
            and not expr.args
            and not expr.keywords
        ):
            raise ValueError("R6D prompt expression changed")

        normalized = expr.func.value.value.strip()
        if sha256(normalized.encode("utf-8")).hexdigest() != expected:
            raise ValueError("R6D approved review prompt changed")

    names = (
        "DEFAULT_TEMPLE_CONTEXT_SANITIZER_MODEL",
        "TEMPLE_CONTEXT_DEIDENTIFICATION_VERSION",
        "TEMPLE_CONTEXT_AGGREGATION_VERSION",
    )
    constants = {}
    for node in tree.body:
        if not isinstance(node, _ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, _ast.Name) and target.id in names:
                if (
                    target.id in constants
                    or not isinstance(node.value, _ast.Constant)
                    or type(node.value.value) is not str
                ):
                    raise ValueError("R6D contract constant changed")
                constants[target.id] = node.value.value

    if set(constants) != set(names):
        raise ValueError("R6D contract constants missing")

    return constants


def resolve_temple_context_contract(
    *, main_source_path, sanitizer_model, deidentification_version,
    aggregation_version, embedding_model, policy,
):
    path = _Path(main_source_path).resolve()
    if path.name != "main.py" or not path.is_file():
        raise ValueError("R6D source authority unavailable")

    tree = _ast.parse(path.read_text(encoding="utf-8"))
    constants = _verify_review_source(tree)

    effective_model = (
        (_os.getenv("TEMPLE_CONTEXT_SANITIZER_MODEL") or "").strip()
        or constants["DEFAULT_TEMPLE_CONTEXT_SANITIZER_MODEL"]
    )
    if (
        sanitizer_model != effective_model
        or deidentification_version
           != constants["TEMPLE_CONTEXT_DEIDENTIFICATION_VERSION"]
        or aggregation_version
           != constants["TEMPLE_CONTEXT_AGGREGATION_VERSION"]
    ):
        raise ValueError("R6D runtime review contract mismatch")

    extraction_version = _contract_hash({
        "schema": "r6d-extraction-code-v1",
        "functions": {
            k: _APPROVED_FUNCTIONS[k]
            for k in (
                "extract_temple_context_theme",
                "_validate_temple_context_extract_payload",
                "_corpus_identifier_flags",
                "_execute_temple_context_openai",
                "_parse_corpus_sanitizer_json",
            )
        },
        "prompt": _APPROVED_PROMPTS["extraction_system"],
    })

    review_version = _contract_hash({
        "schema": "r6d-review-code-v1",
        "functions": {
            k: _APPROVED_FUNCTIONS[k]
            for k in (
                "_validate_temple_context_review_payload",
                "_corpus_identifier_flags",
                "_execute_temple_context_openai",
                "_parse_corpus_sanitizer_json",
            )
        },
        "prompt": _APPROVED_PROMPTS["review_system"],
    })

    review_fingerprint = _contract_hash({
        "schema": "r6d-private-review-provenance-v1",
        "sanitizer_model": sanitizer_model,
        "deidentification_version": deidentification_version,
        "aggregation_version": aggregation_version,
        "extraction_version": extraction_version,
        "review_version": review_version,
    })

    publication_fingerprint = fingerprint_temple_context_contract(
        sanitizer_model=sanitizer_model,
        deidentification_version=deidentification_version,
        aggregation_version=aggregation_version,
        embedding_model=embedding_model,
        extraction_contract_version=extraction_version,
        privacy_review_contract_version=review_version,
        policy=policy,
    )

    return {
        "review_contract_fingerprint": review_fingerprint,
        "publication_contract_fingerprint": publication_fingerprint,
    }
