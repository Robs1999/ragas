from __future__ import annotations

import logging
import typing as t
from dataclasses import dataclass, field

import numpy as np
from datasets import Dataset
from langchain.callbacks.manager import CallbackManager, trace_as_chain_group

from ragas.llms.prompt import Prompt
from ragas.metrics.base import EvaluationMode, MetricWithLLM
from ragas.utils import json_loader

if t.TYPE_CHECKING:
    from langchain.callbacks.base import Callbacks

logger = logging.getLogger(__name__)

CONTEXT_RECALL_RA = Prompt(
    name="context_recall",
    instruction="""Given a context, and an answer, analyze each sentence in the answer and classify if the sentence can be attributed to the given context or not. Use only "Yes" (1) or "No" (0) as a binary classification. Output json with reason.""",
    examples=[
        {
            "question": """What can you tell me about albert Albert Einstein?""",
            "context": """Albert Einstein (14 March 1879 – 18 April 1955) was a German-born theoretical physicist, widely held to be one of the greatest and most influential scientists of all time. Best known for developing the theory of relativity, he also made important contributions to quantum mechanics, and was thus a central figure in the revolutionary reshaping of the scientific understanding of nature that modern physics accomplished in the first decades of the twentieth century. His mass–energy equivalence formula E = mc2, which arises from relativity theory, has been called 'the world's most famous equation'. He received the 1921 Nobel Prize in Physics 'for his services to theoretical physics, and especially for his discovery of the law of the photoelectric effect', a pivotal step in the development of quantum theory. His work is also known for its influence on the philosophy of science. In a 1999 poll of 130 leading physicists worldwide by the British journal Physics World, Einstein was ranked the greatest physicist of all time. His intellectual achievements and originality have made Einstein synonymous with genius.""",
            "answer": """Albert Einstein born in 14 March 1879 was  German-born theoretical physicist, widely held to be one of the greatest and most influential scientists of all time. He received the 1921 Nobel Prize in Physics for his services to theoretical physics. He published 4 papers in 1905.  Einstein moved to Switzerland in 1895""",
            "classification": """[
            {
                "statement_1":"Albert Einstein, born on 14 March 1879, was a German-born theoretical physicist, widely held to be one of the greatest and most influential scientists of all time.",
                "reason": "The date of birth of Einstein is mentioned clearly in the context.",
                "Attributed": "1"
            },
            {
                "statement_2":"He received the 1921 Nobel Prize in Physics 'for his services to theoretical physics.",
                "reason": "The exact sentence is present in the given context.",
                "Attributed": "1"
            },
            {
                "statement_3": "He published 4 papers in 1905.",
                "reason": "There is no mention about papers he wrote in the given context.",
                "Attributed": "0"
            },
            {
                "statement_4":"Einstein moved to Switzerland in 1895.",
                "reason": "There is no supporting evidence for this in the given context.",
                "Attributed": "0"
            }]
            """,
        },
        {
            "question": """who won 2020 icc world cup?""",
            "context": """Who won the 2022 ICC Men's T20 World Cup?""",
            "answer": """England""",
            "classification": """[
            {
                "statement_1":"England won the 2022 ICC Men's T20 World Cup.",
                "reason": "From context it is clear that England defeated Pakistan to win the World Cup.",
                 "Attributed": "1"
            }]
            """,
        },
    ],
    input_keys=["question", "context", "answer"],
    output_key="classification",
    output_type="json",
)


@dataclass
class ContextRecall(MetricWithLLM):

    """
    Estimates context recall by estimating TP and FN using annotated answer and
    retrieved context.

    Attributes
    ----------
    name : str
    batch_size : int
        Batch size for openai completion.
    """

    name: str = "context_recall"  # type: ignore
    evaluation_mode: EvaluationMode = EvaluationMode.qcg  # type: ignore
    context_recall_prompt: Prompt = field(default_factory=lambda: CONTEXT_RECALL_RA)
    batch_size: int = 15

    def adapt(self, language: str, cache_dir: str | None = None) -> None:
        logger.info(f"Adapting Context Recall to {language}")
        self.context_recall_prompt = self.context_recall_prompt.adapt(
            language, self.llm, cache_dir
        )

    def save(self, cache_dir: str | None = None) -> None:
        self.context_recall_prompt.save(cache_dir)

    def _score_batch(
        self: t.Self,
        dataset: Dataset,
        callbacks: t.Optional[Callbacks] = None,
        callback_group_name: str = "batch",
    ) -> list:
        prompts = []
        question, ground_truths, contexts = (
            dataset["question"],
            dataset["ground_truths"],
            dataset["contexts"],
        )

        cb = CallbackManager.configure(inheritable_callbacks=callbacks)
        with trace_as_chain_group(
            callback_group_name, callback_manager=cb
        ) as batch_group:
            for qstn, gt, ctx in zip(question, ground_truths, contexts):
                gt = "\n".join(gt) if isinstance(gt, list) else gt
                ctx = "\n".join(ctx) if isinstance(ctx, list) else ctx
                prompts.append(
                    self.context_recall_prompt.format(
                        question=qstn, context=ctx, answer=gt
                    )
                )

            responses: list[list[str]] = []
            results = self.llm.generate(
                prompts,
                n=1,
                callbacks=batch_group,
            )
            responses = [[i.text for i in r] for r in results.generations]
            scores = []
            for response in responses:
                response = json_loader.safe_load(response[0], self.llm)
                if response:
                    response = [
                        int(item.get("Attributed", "0").strip() == "1")
                        if item.get("Attributed")
                        else np.nan
                        for item in response
                    ]
                    denom = len(response)
                    numerator = sum(response)
                    scores.append(numerator / denom)
                else:
                    scores.append(np.nan)

        return scores


context_recall = ContextRecall()
