from __future__ import annotations

import logging
import typing as t
from dataclasses import dataclass, field

import numpy as np
from datasets import Dataset
from langchain.callbacks.manager import CallbackManager, trace_as_chain_group
from langchain.embeddings import OpenAIEmbeddings

from ragas.embeddings.base import embedding_factory
from ragas.exceptions import OpenAIKeyNotFound
from ragas.llms.prompt import Prompt
from ragas.metrics.base import EvaluationMode, MetricWithLLM
from ragas.utils import json_loader

logger = logging.getLogger(__name__)

if t.TYPE_CHECKING:
    from langchain.callbacks.base import Callbacks

    from ragas.embeddings.base import RagasEmbeddings

QUESTION_GEN = Prompt(
    name="question_generation",
    instruction="""Generate a question for the given answer and Identify if answer is noncommittal""",
    examples=[
        {
            "answer": """Albert Einstein was born in Germany.""",
            "context": """Albert Einstein was a German-born theoretical physicist who is widely held to be one of the greatest and most influential scientists of all time""",
            "output": """{"question":"Where was Albert Einstein born?","noncommittal":false}""",
        },
        {
            "answer": """It can change its skin color based on the temperature of its environment.""",
            "context": """A recent scientific study has discovered a new species of frog in the Amazon rainforest that has the unique ability to change its skin color based on the temperature of its environment.""",
            "output": """{"question":"What unique ability does the newly discovered species of frog have?","noncommittal":false}""",
        },
        {
            "answer": """Everest""",
            "context": """The tallest mountain on Earth, measured from sea level, is a renowned peak located in the Himalayas.""",
            "output": """{"question":"What is the tallest mountain on Earth?","noncommittal":false}""",
        },
        {
            "answer": """I don't know about the  groundbreaking feature of the smartphone invented in 2023 as am unware of information beyond 2022. """,
            "context": """In 2023, a groundbreaking invention was announced: a smartphone with a battery life of one month, revolutionizing the way people use mobile technology.""",
            "output": """{"question":"What was the groundbreaking feature of the smartphone invented in 2023?", "noncommittal":true}""",
        },
    ],
    input_keys=["answer", "context"],
    output_key="output",
    output_type="json",
)


@dataclass
class AnswerRelevancy(MetricWithLLM):
    """
    Scores the relevancy of the answer according to the given question.
    Answers with incomplete, redundant or unnecessary information is penalized.
    Score can range from 0 to 1 with 1 being the best.

    Attributes
    ----------
    name: string
        The name of the metrics
    batch_size: int
        batch size for evaluation
    strictness: int
        Here indicates the number questions generated per answer.
        Ideal range between 3 to 5.
    embeddings: Embedding
        The langchain wrapper of Embedding object.
        E.g. HuggingFaceEmbeddings('BAAI/bge-base-en')
    """

    name: str = "answer_relevancy"  # type: ignore
    evaluation_mode: EvaluationMode = EvaluationMode.qac  # type: ignore
    question_generation: Prompt = field(default_factory=lambda: QUESTION_GEN)
    batch_size: int = 15
    strictness: int = 3
    embeddings: RagasEmbeddings = field(default_factory=embedding_factory)

    def init_model(self):
        super().init_model()

        if isinstance(self.embeddings, OpenAIEmbeddings):
            if self.embeddings.openai_api_key == "no-key":
                raise OpenAIKeyNotFound

    def adapt(self, language: str, cache_dir: str | None = None) -> None:
        logger.info(f"Adapting AnswerRelevancy metric to {language}")
        self.question_generation = self.question_generation.adapt(
            language, self.llm, cache_dir
        )

    def save(self, cache_dir: str | None = None) -> None:
        self.question_generation.save(cache_dir)

    def _score_batch(
        self: t.Self,
        dataset: Dataset,
        callbacks: t.Optional[Callbacks] = None,
        callback_group_name: str = "batch",
    ) -> list[float]:
        questions, answers, contexts = (
            dataset["question"],
            dataset["answer"],
            dataset["contexts"],
        )

        cb = CallbackManager.configure(inheritable_callbacks=callbacks)
        with trace_as_chain_group(
            callback_group_name, callback_manager=cb
        ) as batch_group:
            prompts = []
            for ans, ctx in zip(answers, contexts):
                prompts.append(
                    self.question_generation.format(answer=ans, context="\n".join(ctx))
                )

            results = self.llm.generate(
                prompts,
                n=self.strictness,
                callbacks=batch_group,
            )
            results = [
                [json_loader.safe_load(i.text, self.llm) for i in r]
                for r in results.generations
            ]
            scores = []
            for question, result in zip(questions, results):
                gen_questions = [item.get("question", "") for item in result]
                committal = np.any([item.get("noncommittal", False) for item in result])
                cosine_sim = self.calculate_similarity(question, gen_questions)
                scores.append(cosine_sim.mean() * int(not committal))

        return scores

    def calculate_similarity(
        self: t.Self, question: str, generated_questions: list[str]
    ):
        assert self.embeddings is not None
        question_vec = np.asarray(self.embeddings.embed_query(question)).reshape(1, -1)
        gen_question_vec = np.asarray(
            self.embeddings.embed_documents(generated_questions)
        )
        norm = np.linalg.norm(gen_question_vec, axis=1) * np.linalg.norm(
            question_vec, axis=1
        )
        return (
            np.dot(gen_question_vec, question_vec.T).reshape(
                -1,
            )
            / norm
        )


answer_relevancy = AnswerRelevancy()
