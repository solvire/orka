import os
import logging
from typing import Optional

# Core Orka Tools
from orka.core.ingester import OrkaGraphDB
from orka.surgery.synthesizer import extract_method_source, build_synthesis_prompt
from orka.surgery.modifier import apply_llm_patch

# Standalone Orka LLM client
from orka.clients import OrkaLangChainClient

logger = logging.getLogger("Orchestrator")

class Orchestrator:
    def __init__(self, workspace_dir: str, provider: str = "together_ai"):
        self.workspace_dir = workspace_dir
        
        # 1. Initialize Dual-Brain (Topology + Semantic)
        self.graph_db = OrkaGraphDB(cache_file=os.path.join(workspace_dir, ".orka_cache.json"))
        logger.info("Initializing Orka Brain...")
        self.graph_db.scan_directory(workspace_dir)
        
        # 2. Initialize the Scalpel (Standalone OrkaLangChainClient)
        logger.info(f"Loading {provider.upper()} client via OrkaLangChainClient...")
        self.llm_client = OrkaLangChainClient(provider=provider)

    def _get_graph_constraints(self, class_name: str, method_name: str) -> str:
        """Queries NetworkX graph to find incoming connections to warn the LLM."""
        target_node = None
        for node in self.graph_db.graph.nodes:
            if node.endswith(f".{class_name}.{method_name}") or node.endswith(f".{method_name}"):
                if self.graph_db.graph.nodes[node].get("node_type") == "method":
                    target_node = node
                    break
        
        if not target_node:
            return "No graph constraints found."

        inward_edges = list(self.graph_db.graph.predecessors(target_node))
        if not inward_edges:
            return "No known callers in the internal graph."

        callers = [n.split(":")[-1] for n in inward_edges]
        return f"CRITICAL: This method is called by {len(callers)} other components (e.g., {', '.join(callers[:3])}). DO NOT change the expected return type."

    def refactor_method(self, file_path: str, class_name: str, method_name: str, requirements: str) -> bool:
        """The Scalpel Pipeline: Uses Direct API to surgically update a method."""
        logger.info(f"Targeting {class_name}.{method_name} in {file_path}")

        # 1. Gather Dependencies
        constraints = self._get_graph_constraints(class_name, method_name)

        # 2. Extract Source
        existing_code = extract_method_source(file_path, method_name, class_name)
        if not existing_code:
            logger.error(f"Failed to extract method source. Does {class_name}.{method_name} exist?")
            return False

        # 3. Build Strict Prompt
        prompt = build_synthesis_prompt(
            existing_code=existing_code,
            business_requirements=requirements,
            graph_constraints=constraints
        )
        
        system_instruction = "You are a pure code synthesis engine. Output ONLY raw Python code at the base indentation level. Do not include signatures, decorators, or explanations."

        # 4. Invoke LLM Client Directly
        logger.info(f"Invoking {self.llm_client.provider.upper()} API (Scalpel Mode)...")
        try:
            raw_output = self.llm_client.generate_code(
                prompt=prompt,
                system_instruction=system_instruction
            )
            
            # Clean the output using the static method
            clean_logic = OrkaLangChainClient.fix_md_fences(raw_output)
            
        except Exception as e:
            logger.error(f"LLM Client Synthesis Failed: {e}")
            return False

        # 5. Apply Surgical Patch
        logger.info("Applying LibCST Patch...")
        success = apply_llm_patch(file_path, method_name, clean_logic, class_name)
        
        if success:
            logger.info(f"Successfully refactored {method_name}!")
        else:
            logger.error(f"LibCST failed to apply patch to {method_name}. Check indentation or syntax.")
            
        return success