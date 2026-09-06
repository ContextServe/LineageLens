"""Hybrid Graph Merger for combining Tree-sitter base graphs with SCIP compiler facts."""

from __future__ import annotations

import logging

from lineagelens.model import CodeGraph, Evidence, Relation

logger = logging.getLogger(__name__)


class HybridGraphMerger:
    """Merges Tree-sitter baseline graph with SCIP compiler facts."""

    @staticmethod
    def merge(base_graph: CodeGraph, scip_graph: CodeGraph) -> CodeGraph:
        """Enrich baseline Tree-sitter CodeGraph with compiler facts from SCIP."""
        merged_graph = CodeGraph(
            project_root=base_graph.project_root,
            symbols=dict(base_graph.symbols),
            containers=dict(base_graph.containers),
            relations=list(base_graph.relations),
        )

        # 1. Merge SCIP Containers and Symbols if missing
        for cid, container in scip_graph.containers.items():
            if cid not in merged_graph.containers:
                merged_graph.add_container(container)

        for sym_id, symbol in scip_graph.symbols.items():
            if sym_id not in merged_graph.symbols:
                merged_graph.add_symbol(symbol)

        # 2. Reconcile Relations & Upgrade Evidence
        existing_relations_map: dict[tuple[str, str, int], Relation] = {
            (r.source, r.target, r.line): r for r in merged_graph.relations
        }

        # Also map by (source_file, line, target_name) for fuzzy matching
        fuzzy_relations_map: dict[tuple[str, int, str], Relation] = {}
        for r in merged_graph.relations:
            target_name = r.target.rsplit("::", 1)[-1]
            fuzzy_relations_map[(r.file, r.line, target_name)] = r

        scip_facts_added = 0
        relations_upgraded = 0

        for scip_rel in scip_graph.relations:
            key = (scip_rel.source, scip_rel.target, scip_rel.line)
            scip_target_name = scip_rel.target.rsplit("::", 1)[-1]
            fuzzy_key = (scip_rel.file, scip_rel.line, scip_target_name)

            if key in existing_relations_map:
                existing_rel = existing_relations_map[key]
                existing_rel.evidence = Evidence(tier="deterministic_fact", label="scip_compiler")
                existing_rel.resolution = "resolved"
                existing_rel.resolution_evidence = Evidence(tier="deterministic_fact", label="scip_symbol_reference")
                relations_upgraded += 1
            elif fuzzy_key in fuzzy_relations_map:
                existing_rel = fuzzy_relations_map[fuzzy_key]
                existing_rel.target = scip_rel.target
                existing_rel.evidence = Evidence(tier="deterministic_fact", label="scip_compiler")
                existing_rel.resolution = "resolved"
                existing_rel.resolution_evidence = Evidence(tier="deterministic_fact", label="scip_symbol_reference")
                relations_upgraded += 1
            else:
                merged_graph.add_relation(scip_rel)
                scip_facts_added += 1

        logger.info(
            f"Hybrid Merger Complete: {relations_upgraded} relations upgraded to scip_compiler facts, "
            f"{scip_facts_added} new SCIP relations added."
        )

        return merged_graph
