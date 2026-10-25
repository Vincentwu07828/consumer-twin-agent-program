"""Explicit lexical fallback adapter for the original LifeSim KVMemory interface.

No embedding API, model download, or model training. Not semantic/vector retrieval.
"""
import copy
import math
import re
from collections import Counter


class TextRetriever:
    def __init__(self, **config):
        self.config = config
        self.documents = []

    def reset_collection(self):
        self.documents = []

    def build_index(self, data):
        self.documents = copy.deepcopy(data)

    def add_documents(self, data):
        self.documents.extend(copy.deepcopy(data))

    def search(self, query, top_k=3, return_scores=True):
        def tokens(text):
            words = re.findall(r"[a-z0-9]+", text.casefold())
            chars = "".join(re.findall(r"[\u4e00-\u9fff]", text))
            return Counter(words + [chars[i:i + 2] for i in range(max(0, len(chars) - 1))])
        q = tokens(query)
        scored = []
        for position, document in enumerate(self.documents):
            d = tokens(document.get("text", ""))
            denominator = math.sqrt(sum(x*x for x in q.values()) * sum(x*x for x in d.values()))
            score = sum(value * d.get(key, 0) for key, value in q.items()) / denominator if denominator else 0
            scored.append((score, position, document))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [{"data": copy.deepcopy(d), "score": float(score)} for score, _, d in scored[:top_k]]
