# Phase 10 Ollama / LLaMA Service Retirement Notes

## Current production retrieval path

Production is now running:

RETRIEVAL_BACKEND=pgvector
PGVECTOR_RETRIEVAL_LIMIT=5
LLAMA_PHASE1_ENABLED=false

## Reason for retiring active Ollama hosting

The Ollama/LLaMA Phase1 service required an 8 GB instance due to the local model footprint. With Phase1 disabled, the Ollama service is no longer part of the active seeker path.

## Observed production behavior after pgvector

Production logs showed pgvector retrieval generally under about 2 seconds, LLaMA Phase1 disabled, fast Moses text responses, and much faster anon voice response flow.

## LLaMA tradeoff

LLaMA Phase1 was intended to filter retrieved passages, produce a compact brief, recommend context budget, and potentially reduce final API token spend.

It was disabled because blocking Phase1 introduced 30-45 second delays and sometimes timed out or fail-opened.

## Retirement decision

The Ollama service may be suspended or deleted to remove the memory footprint and cost burden.

Keep the LLaMA Python code for now as dormant/future experimental code. Do not remove it until token/cost logging exists, pgvector production path has been stable, and any future LLaMA use is redesigned as shadow/background or strict-timeout only.
