import os
import chromadb
import logging

logger = logging.getLogger("OrkaVectorDB")

class OrkaVectorDB:
    def __init__(self, persist_dir: str = ".orka_chromadb"):
        # Suppress ChromaDB's noisy telemetry/startup logs
        os.environ["CHROMA_SERVER_NOFILE"] = "65535" 
        
        self.client = chromadb.PersistentClient(path=persist_dir)
        # We use a single collection for the codebase
        self.collection = self.client.get_or_create_collection(
            name="orka_semantic_graph",
            metadata={"hnsw:space": "cosine"} # Cosine similarity works best for code
        )

    def upsert_node(self, node_id: str, source_code: str, file_path: str, node_type: str):
        """Embeds a python component into the vector database."""
        if not source_code or not source_code.strip():
            return
            
        self.collection.upsert(
            ids=[node_id],
            documents=[source_code],
            metadatas=[{"file_path": file_path, "node_type": node_type}]
        )

    def delete_file_nodes(self, file_path: str):
        """Removes all embeddings associated with a file (used during re-ingestion)."""
        try:
            self.collection.delete(where={"file_path": file_path})
        except Exception:
            # If the file doesn't exist in Chroma yet, just pass
            pass

    def search(self, query: str, n_results: int = 3, node_type: str = None) -> list:
        """
        Semantic search. Optionally filter by node_type ('method', 'class', 'function').
        Returns a list of dicts with id, document, and metadata.
        """
        where_clause = {"node_type": node_type} if node_type else None
        
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_clause
        )
        
        formatted_results = []
        if results and results['ids']:
            for i in range(len(results['ids'][0])):
                formatted_results.append({
                    "id": results['ids'][0][i],
                    "source": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "distance": results['distances'][0][i] if 'distances' in results else None
                })
        return formatted_results