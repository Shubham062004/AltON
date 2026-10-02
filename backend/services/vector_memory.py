import os
import json
import sqlite3
import numpy as np
from typing import List, Dict, Any

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    faiss = None
    FAISS_AVAILABLE = False


class VectorMemoryAgent:
    """
    Advanced Memory Agent for ULTRON OS.
    Combines SQLite persistence with FAISS / Cosine Similarity vector embeddings
    for semantic retrieval over user facts, project notes, and documents.
    """

    def __init__(self, db_path: str = None):
        if not db_path:
            db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ultron_memory.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS semantic_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                embedding TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def _compute_embedding(self, text: str) -> np.ndarray:
        """
        Computes a 384-dimensional normalized vector embedding for text.
        Tries SentenceTransformer BAAI/bge-small-en-v1.5 if available;
        falls back to deterministic character n-gram hashing vectorizer.
        """
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("BAAI/bge-small-en-v1.5")
            emb = model.encode(text)
            norm = np.linalg.norm(emb)
            return (emb / (norm + 1e-8)).astype(np.float32)
        except Exception:
            # Deterministic 384-dim hash-based bag-of-ngrams fallback
            vec = np.zeros(384, dtype=np.float32)
            words = text.lower().split()
            for word in words:
                for i in range(len(word)):
                    sub = word[i:i+3]
                    idx = hash(sub) % 384
                    vec[idx] += 1.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec

    def store_memory(self, content: str, category: str = "general") -> Dict[str, Any]:
        if not content or not content.strip():
            return {"status": "error", "message": "Memory content cannot be empty."}

        text = content.strip()
        emb_vec = self._compute_embedding(text)
        emb_str = json.dumps(emb_vec.tolist())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO semantic_memories (content, category, embedding) VALUES (?, ?, ?)",
            (text, category, emb_str)
        )
        conn.commit()
        memory_id = cursor.lastrowid
        conn.close()

        return {
            "status": "success",
            "id": memory_id,
            "content": text,
            "category": category,
            "vector_dim": len(emb_vec)
        }

    def search_memory(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []

        q_vec = self._compute_embedding(query.strip())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, content, category, embedding, created_at FROM semantic_memories")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        ids, contents, categories, dates, vectors = [], [], [], [], []
        for r in rows:
            mid, content, cat, emb_json, date_val = r
            if emb_json:
                try:
                    vec = np.array(json.loads(emb_json), dtype=np.float32)
                    ids.append(mid)
                    contents.append(content)
                    categories.append(cat)
                    dates.append(date_val)
                    vectors.append(vec)
                except Exception:
                    continue

        if not vectors:
            return []

        matrix = np.vstack(vectors)

        # Use FAISS index if available
        if FAISS_AVAILABLE and faiss is not None:
            try:
                index = faiss.IndexFlatIP(matrix.shape[1])
                index.add(matrix)
                distances, indices = index.search(np.array([q_vec]), min(top_k, len(vectors)))
                results = []
                for idx, dist in zip(indices[0], distances[0]):
                    if idx < len(contents):
                        results.append({
                            "id": ids[idx],
                            "content": contents[idx],
                            "category": categories[idx],
                            "score": float(dist),
                            "created_at": dates[idx]
                        })
                return results
            except Exception:
                pass

        # Fallback NumPy Cosine Similarity
        scores = np.dot(matrix, q_vec)
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            results.append({
                "id": ids[idx],
                "content": contents[idx],
                "category": categories[idx],
                "score": float(scores[idx]),
                "created_at": dates[idx]
            })

        return results

    def list_memories(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, content, category, created_at FROM semantic_memories ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        return [{"id": r[0], "content": r[1], "category": r[2], "created_at": r[3]} for r in rows]

    def delete_memory(self, memory_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM semantic_memories WHERE id = ?", (memory_id,))
        conn.commit()
        count = cursor.rowcount
        conn.close()
        return count > 0
