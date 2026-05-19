"""
长期记忆 — ChromaDB 向量存储，将客户诉求/意向转化为 Embeddings
实现跨会话的「长效学习与记忆」能力
"""
import logging
import time

logger = logging.getLogger(__name__)

# 延迟导入 chromadb，避免未安装时阻断启动
_chromadb = None
_collection = None
_initialized = False


def _init_chromadb(persist_dir: str = "./assets/chromadb"):
    """懒加载初始化 ChromaDB"""
    global _chromadb, _collection, _initialized
    if _initialized:
        return

    try:
        import chromadb
        from chromadb.config import Settings

        _chromadb = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _chromadb.get_or_create_collection(
            name="customer_memory",
            metadata={"hnsw:space": "cosine"},
        )
        _initialized = True
        logger.info(f"ChromaDB 向量库已初始化: {persist_dir}")
    except ImportError:
        logger.warning("chromadb 未安装，长期记忆功能不可用。请运行: pip install chromadb")
        _initialized = False
    except Exception as e:
        logger.error(f"ChromaDB 初始化失败: {e}")
        _initialized = False


class LongTermMemory:
    """
    基于 ChromaDB 的向量长期记忆。
    - 存储：将客户的诉求、购买意向转化为 Embeddings
    - 召回：再次对话时，提取最相关的 Top-K 历史记录
    """

    def __init__(self, persist_dir: str = "./assets/chromadb"):
        self.persist_dir = persist_dir
        _init_chromadb(persist_dir)

    @property
    def available(self) -> bool:
        return _initialized and _collection is not None

    def store(self, user_id: int, text: str, metadata: dict = None):
        """
        将一段文本存入向量库。

        Args:
            user_id: 用户 ID
            text: 要存储的文本（如客户诉求/对话摘要）
            metadata: 附加元数据
        """
        if not self.available:
            return

        try:
            doc_id = f"u{user_id}_{int(time.time() * 1000)}"
            meta = {"user_id": str(user_id), "timestamp": time.time()}
            if metadata:
                meta.update(metadata)

            _collection.add(
                documents=[text],
                metadatas=[meta],
                ids=[doc_id],
            )
            logger.debug(f"向量存储成功: user={user_id}, doc_id={doc_id}")
        except Exception as e:
            logger.error(f"向量存储失败: {e}")

    def recall(self, user_id: int, query: str, top_k: int = 5) -> list[str]:
        """
        根据查询文本，召回该用户最相关的 Top-K 历史记录。

        Args:
            user_id: 用户 ID
            query: 查询文本
            top_k: 返回数量

        Returns:
            最相关的历史文本列表
        """
        if not self.available:
            return []

        try:
            results = _collection.query(
                query_texts=[query],
                n_results=top_k,
                where={"user_id": str(user_id)},
            )

            documents = results.get("documents", [[]])[0]
            return documents if documents else []
        except Exception as e:
            logger.error(f"向量召回失败: {e}")
            return []

    def get_user_summary(self, user_id: int, query: str, top_k: int = 5) -> str:
        """
        获取格式化的用户历史摘要，直接可作为 LLM 上下文。
        """
        docs = self.recall(user_id, query, top_k)
        if not docs:
            return ""

        lines = [f"• {doc}" for doc in docs]
        return "\n".join(lines)
