import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

from openai_agents.workflows.redpanda_activity import publish_workflow_event
from openai_agents.workflows.research_agents.research_manager import (
    InteractiveResearchManager,
)
from openai_agents.workflows.research_agents.research_models import (
    ClarificationInput,
    ResearchInteractionDict,
    SingleClarificationInput,
    UserQueryInput,
)


@dataclass
class ProcessClarificationInput:
    """Input for clarification processing activity"""

    answer: str
    current_question_index: int
    current_question: str | None
    total_questions: int


@dataclass
class ProcessClarificationResult:
    """Result from clarification processing activity"""

    question_key: str
    answer: str
    new_index: int


@dataclass
class KnowledgeGraphExactMatchInput:
    """Input for knowledge graph exact match activity"""

    query: str
    min_score: float = 0.8


@dataclass
class KnowledgeGraphExactMatchResult:
    """Result from knowledge graph exact match activity"""

    found: bool
    short_summary: str | None = None
    markdown_report: str | None = None
    score: float | None = None
    result_id: str | None = None  # ID of the existing Result node in Neo4j
    image_file_path: str | None = None  # Image path from existing Result node
    title: str | None = None


@dataclass
class KnowledgeGraphSearchInput:
    """Input for knowledge graph search activity"""

    query: str
    limit: int = 3
    min_score: float = 0.5


@dataclass
class KnowledgeGraphSearchResult:
    """Result from knowledge graph search activity"""

    context: str


@activity.defn
async def check_knowledge_graph_exact_match(
    input: KnowledgeGraphExactMatchInput,
) -> KnowledgeGraphExactMatchResult:
    """Check if there's an exact match in the knowledge graph"""
    try:
        from openai_agents.memory.neo4j_rag import get_neo4j_rag

        activity.logger.info(
            f"🔍 Checking knowledge graph for exact match (>= {input.min_score}) for query: '{input.query[:50]}...'"
        )
        rag = await get_neo4j_rag()
        if not rag:
            activity.logger.warning("⚠️ Neo4j RAG not available")
            return KnowledgeGraphExactMatchResult(found=False)

        # Always fetch the best match at a low threshold so we can log the actual score
        best = await rag.get_best_match(input.query, min_score=0.3)
        if best:
            node_dict, score = best
            result_id = node_dict.get("result_id")
            image_file_path = node_dict.get("image_file_path")
            title = node_dict.get("title", "N/A")
            activity.logger.info(
                f"📊 Best match: score={score:.4f}, title='{title[:50]}', threshold={input.min_score}"
            )
            if score >= input.min_score:
                activity.logger.info(
                    f"🎯 Score {score:.4f} >= {input.min_score} — returning match! Result ID: {result_id}, Image: {image_file_path}"
                )
                return KnowledgeGraphExactMatchResult(
                    found=True,
                    short_summary=node_dict.get("short_summary", ""),
                    markdown_report=node_dict.get("markdown_report", ""),
                    score=score,
                    result_id=result_id,
                    image_file_path=image_file_path,
                    title=node_dict.get("title", ""),
                )
            else:
                activity.logger.info(
                    f"❌ Score {score:.4f} < {input.min_score} — not a match"
                )
                return KnowledgeGraphExactMatchResult(found=False)
        else:
            activity.logger.info(
                f"❌ No results found in knowledge graph at all"
            )
            return KnowledgeGraphExactMatchResult(found=False)
    except Exception as e:
        activity.logger.error(f"❌ Knowledge graph exact match check failed: {e}")
        import traceback

        traceback.print_exc()
        return KnowledgeGraphExactMatchResult(found=False)


@activity.defn
async def search_knowledge_graph(
    input: KnowledgeGraphSearchInput,
) -> KnowledgeGraphSearchResult:
    """Search knowledge graph for relevant context"""
    try:
        from openai_agents.memory.neo4j_rag import get_neo4j_rag

        activity.logger.info(
            f"🔍 Searching knowledge graph for context (min_score: {input.min_score}) for query: '{input.query[:50]}...'"
        )
        rag = await get_neo4j_rag()
        if not rag:
            activity.logger.warning("⚠️ Neo4j RAG not available")
            return KnowledgeGraphSearchResult(context="")

        context = await rag.get_relevant_context(
            input.query, limit=input.limit, min_score=input.min_score
        )
        activity.logger.info(
            f"✅ Retrieved {len(context)} characters of context from knowledge graph"
        )
        return KnowledgeGraphSearchResult(context=context)
    except Exception as e:
        activity.logger.error(f"❌ Knowledge graph search failed: {e}")
        import traceback

        traceback.print_exc()
        return KnowledgeGraphSearchResult(context="")


@activity.defn
async def process_clarification(
    input: ProcessClarificationInput,
) -> ProcessClarificationResult:
    """Process a single clarification answer"""
    activity.logger.info(
        f"Processing clarification answer {input.current_question_index + 1}/{input.total_questions}: "
        f"'{input.answer}' for question: '{input.current_question}'"
    )

    # Simulate cloud provider outages for the second-to-last question
    is_second_last_question = (
        input.current_question_index + 2
    ) == input.total_questions
    if is_second_last_question:
        attempt = activity.info().attempt
        if attempt == 1:
            raise ApplicationError("Simulated failure -- try again soon :)")
        elif attempt <= 3:
            await asyncio.sleep(10)
            raise ApplicationError("Simulated failure -- try again soon :)")

    question_key = f"question_{input.current_question_index}"
    return ProcessClarificationResult(
        question_key=question_key,
        answer=input.answer,
        new_index=input.current_question_index + 1,
    )


@dataclass
class InteractiveResearchResult:
    """Result from interactive research workflow including both markdown"""

    short_summary: str
    markdown_report: str
    follow_up_questions: list[str]
    image_file_path: str | None = None
    existing_result_id: str | None = (
        None  # ID of existing Result node if reused from knowledge graph
    )


@workflow.defn
class InteractiveResearchWorkflow:
    def __init__(self) -> None:
        self.research_manager = InteractiveResearchManager()
        # Simple instance variables instead of complex dataclass
        self.original_query: str | None = None
        self.clarification_questions: list[str] = []
        self.clarification_responses: dict[str, str] = {}
        self.current_question_index: int = 0
        self.report_data: Any | None = None
        self.research_completed: bool = False
        self.workflow_ended: bool = False
        self.research_initialized: bool = False
        self.early_match_found: bool = False
        # Suggestion state for accept/reject flow
        self.suggested_result_id: str | None = None
        self.suggested_result_title: str | None = None
        self.suggested_result_summary: str | None = None
        self.suggested_result_image: str | None = None
        self.suggested_result_data: Any | None = None  # ReportData
        self.suggestion_accepted: bool | None = None  # None=pending, True=accepted, False=rejected
        self.rejected_result_ids: list[str] = []

    def _build_result(
        self,
        summary: str,
        report: str,
        questions: list[str] | None = None,
        image_path: str | None = None,
        existing_result_id: str | None = None,
    ) -> InteractiveResearchResult:
        """Helper to build InteractiveResearchResult"""
        return InteractiveResearchResult(
            short_summary=summary,
            markdown_report=report,
            follow_up_questions=questions or [],
            image_file_path=image_path,
            existing_result_id=existing_result_id,
        )

    @workflow.run
    async def run(
        self, initial_query: str | None = None, use_clarifications: bool = True
    ) -> InteractiveResearchResult:
        """
        Run research workflow - long-running interactive workflow with clarifying questions

        Args:
            initial_query: Optional initial research query (for backward compatibility)
            use_clarifications: If True, enables interactive clarifying questions (for backward compatibility)
        """
        if initial_query and not use_clarifications:
            # Simple direct research mode - backward compatibility
            report_data = await self.research_manager._run_direct(initial_query)
            # Use existing image path if we reused a result, otherwise use newly generated image
            image_path = (
                self.research_manager.existing_image_path
                or self.research_manager.research_image_path
            )
            return self._build_result(
                report_data.short_summary,
                report_data.markdown_report,
                report_data.follow_up_questions,
                image_path,
                self.research_manager.existing_result_id,
            )

        # Main workflow loop - wait for research to be started and completed
        while True:
            workflow.logger.info("Waiting for research to start or complete...")

            # Wait for workflow end signal, research completion, or research initialization
            await workflow.wait_condition(
                lambda: self.workflow_ended
                or self.research_completed
                or self.research_initialized
            )

            workflow.logger.info(
                f"🔔 Main loop woke up: ended={self.workflow_ended}, completed={self.research_completed}, "
                f"initialized={self.research_initialized}, questions={len(self.clarification_questions)}, "
                f"responses={len(self.clarification_responses)}"
            )

            # If workflow was signaled to end, exit gracefully
            if self.workflow_ended:
                return self._build_result(
                    "Research ended by user",
                    "Research workflow ended by user",
                    None,
                    None,
                    None,
                )

            # If research has been completed, return results
            if self.research_completed and self.report_data:
                # Use existing image path if we reused a result, otherwise use newly generated image
                image_path = (
                    self.research_manager.existing_image_path
                    or self.research_manager.research_image_path
                )
                workflow.logger.info(
                    f"Building result with image_path: {image_path}, existing_result_id: {self.research_manager.existing_result_id}"
                )

                # Publish research_complete event
                await workflow.execute_activity(
                    publish_workflow_event,
                    args=[
                        "research_complete",
                        workflow.info().workflow_id,
                        {
                            "report_length": len(self.report_data.markdown_report),
                            "has_image": image_path is not None,
                            "image_path": image_path,
                            "summary": self.report_data.short_summary[:100],
                        },
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                )

                return self._build_result(
                    self.report_data.short_summary,
                    self.report_data.markdown_report,
                    self.report_data.follow_up_questions,
                    image_path,
                    self.research_manager.existing_result_id,
                )

            # If research is initialized but not completed, handle the clarification flow
            if self.research_initialized and not self.research_completed:
                workflow.logger.info(
                    f"📋 Entering clarification flow: has_questions={bool(self.clarification_questions)}, "
                    f"num_questions={len(self.clarification_questions)}, has_report={self.report_data is not None}"
                )
                # If we have clarification questions, check similarity incrementally
                if self.clarification_questions:
                    answers_processed = 0
                    total_questions = len(self.clarification_questions)
                    workflow.logger.info(
                        f"🔄 Starting incremental similarity loop for {total_questions} questions"
                    )

                    while answers_processed < total_questions:
                        # If a suggestion is already pending (from start_research),
                        # skip the similarity check and go straight to waiting
                        if self.suggested_result_id and self.suggestion_accepted is None:
                            exact_match = self.suggested_result_data
                        else:
                            # CHECK SIMILARITY (before waiting for next answer)
                            # On first iteration this checks just the original query;
                            # on subsequent iterations it includes answers collected so far
                            partial_query = self.research_manager._enrich_query_partial(
                                self.original_query,
                                self.clarification_questions,
                                self.clarification_responses,
                            )
                            workflow.logger.info(
                                f"🔍 Checking similarity ({answers_processed}/{total_questions} answers): {partial_query[:200]}..."
                            )

                            exact_match = await self.research_manager._check_knowledge_graph_for_exact_match(
                                partial_query, min_score=0.70
                            )

                        if exact_match:
                            matched_result_id = self.research_manager.existing_result_id

                            # Skip if this result was already rejected in this conversation
                            if matched_result_id in self.rejected_result_ids:
                                workflow.logger.info(
                                    f"⏭️ Skipping previously rejected result {matched_result_id}"
                                )
                                exact_match = None  # Fall through to wait for next answer

                        if exact_match:
                                workflow.logger.info(
                                    f"💡 Suggesting result ({answers_processed}/{total_questions} answers): "
                                    f"{self.research_manager.existing_result_title}"
                                )

                                # Store suggestion and wait for user decision
                                self.suggested_result_id = matched_result_id
                                self.suggested_result_title = self.research_manager.existing_result_title
                                self.suggested_result_summary = exact_match.short_summary
                                self.suggested_result_image = self.research_manager.existing_image_path
                                self.suggested_result_data = exact_match
                                self.suggestion_accepted = None

                                await workflow.execute_activity(
                                    publish_workflow_event,
                                    args=[
                                        "suggestion_offered",
                                        workflow.info().workflow_id,
                                        {
                                            "result_id": matched_result_id,
                                            "title": self.suggested_result_title,
                                            "answers_processed": answers_processed,
                                            "total_questions": total_questions,
                                        },
                                    ],
                                    start_to_close_timeout=timedelta(seconds=10),
                                )

                                # Wait for user to accept or reject
                                await workflow.wait_condition(
                                    lambda: self.suggestion_accepted is not None
                                    or self.workflow_ended
                                )

                                if self.workflow_ended:
                                    break

                                if self.suggestion_accepted:
                                    workflow.logger.info(
                                        f"👍 User accepted suggestion: {matched_result_id}"
                                    )
                                    self.report_data = exact_match
                                    self.research_completed = True
                                    self.early_match_found = True

                                    await workflow.execute_activity(
                                        publish_workflow_event,
                                        args=[
                                            "suggestion_accepted",
                                            workflow.info().workflow_id,
                                            {"result_id": matched_result_id},
                                        ],
                                        start_to_close_timeout=timedelta(seconds=10),
                                    )
                                    break
                                else:
                                    workflow.logger.info(
                                        f"👎 User rejected suggestion: {matched_result_id}"
                                    )
                                    self.rejected_result_ids.append(matched_result_id)
                                    self.suggested_result_id = None
                                    self.suggested_result_title = None
                                    self.suggested_result_summary = None
                                    self.suggested_result_image = None
                                    self.suggested_result_data = None
                                    self.suggestion_accepted = None
                                    self.research_manager.existing_result_id = None
                                    self.research_manager.existing_image_path = None
                                    self.research_manager.existing_result_title = None

                                    await workflow.execute_activity(
                                        publish_workflow_event,
                                        args=[
                                            "suggestion_rejected",
                                            workflow.info().workflow_id,
                                            {"result_id": matched_result_id},
                                        ],
                                        start_to_close_timeout=timedelta(seconds=10),
                                    )

                        # If research was completed (accepted suggestion), exit loop
                        if self.research_completed or self.workflow_ended:
                            break

                        # THEN wait for the next answer
                        workflow.logger.info(
                            f"⏳ Waiting for answer {answers_processed + 1}/{total_questions}..."
                        )
                        target = answers_processed
                        await workflow.wait_condition(
                            lambda: self.workflow_ended
                            or len(self.clarification_responses) > target
                        )

                        if self.workflow_ended:
                            break

                        answers_processed = len(self.clarification_responses)
                        workflow.logger.info(
                            f"📝 Received answer {answers_processed}/{total_questions}"
                        )

                    if self.workflow_ended:
                        return self._build_result(
                            "Research ended by user",
                            "Research workflow ended by user",
                            None,
                            None,
                            None,
                        )

                    # If no early match was found, run full research with all clarifications
                    if not self.research_completed:
                        # Publish clarifications_complete event
                        await workflow.execute_activity(
                            publish_workflow_event,
                            args=[
                                "clarifications_complete",
                                workflow.info().workflow_id,
                                {
                                    "responses": self.clarification_responses,
                                    "total_answered": len(self.clarification_responses),
                                },
                            ],
                            start_to_close_timeout=timedelta(seconds=10),
                        )

                        # Complete research with clarifications
                        if self.original_query:  # Type guard to ensure it's not None
                            # Publish research_started event
                            await workflow.execute_activity(
                                publish_workflow_event,
                                args=[
                                    "research_started",
                                    workflow.info().workflow_id,
                                    {"query": self.original_query},
                                ],
                                start_to_close_timeout=timedelta(seconds=10),
                            )

                            self.report_data = await self.research_manager.run_with_clarifications_complete(
                                self.original_query,
                                self.clarification_questions,
                                self.clarification_responses,
                            )

                        self.research_completed = True
                    continue

                # If we already have report data (from direct research), mark as completed
                elif self.report_data is not None:
                    self.research_completed = True
                    continue

                # If no clarification questions and no report data, it means research failed
                return self._build_result(
                    "No research completed", "Research failed to start properly"
                )

    def _get_current_question(self) -> str | None:
        """Get the current question that needs an answer"""
        if self.current_question_index >= len(self.clarification_questions):
            return None
        return self.clarification_questions[self.current_question_index]

    def _has_more_questions(self) -> bool:
        """Check if there are more questions to answer"""
        return self.current_question_index < len(self.clarification_questions)

    @workflow.query
    def get_status(self) -> ResearchInteractionDict:
        """Get current research status"""
        current_question = self._get_current_question()

        # Determine status based on workflow state
        if self.workflow_ended:
            status = "ended"
        elif self.suggested_result_id and self.suggestion_accepted is None:
            status = "suggesting_result"
        elif self.research_completed and self.early_match_found:
            status = "match_found"
        elif self.research_completed:
            status = "completed"
        elif self.clarification_questions and len(self.clarification_responses) < len(
            self.clarification_questions
        ):
            if len(self.clarification_responses) == 0:
                status = "awaiting_clarifications"
            else:
                status = "collecting_answers"
        elif self.original_query and not self.research_completed:
            status = "researching"
        else:
            status = "pending"

        return ResearchInteractionDict(
            original_query=self.original_query,
            clarification_questions=self.clarification_questions,
            clarification_responses=self.clarification_responses,
            current_question_index=self.current_question_index,
            current_question=current_question,
            status=status,
            research_completed=self.research_completed,
            early_match_found=self.early_match_found,
            suggested_result_id=self.suggested_result_id,
            suggested_result_title=self.suggested_result_title,
            suggested_result_summary=self.suggested_result_summary,
            suggested_result_image=self.suggested_result_image,
        )

    @workflow.update
    async def start_research(self, input: UserQueryInput) -> ResearchInteractionDict:
        """Start a new research session with clarifying questions flow"""
        workflow.logger.info(f"Starting research for query: '{input.query}'")
        self.original_query = input.query

        # Publish query_received event
        await workflow.execute_activity(
            publish_workflow_event,
            args=[
                "query_received",
                workflow.info().workflow_id,
                {"query": input.query},
            ],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Immediately check if clarifications are needed
        result = await self.research_manager.run_with_clarifications_start(
            self.original_query
        )

        if result.needs_clarifications:
            # Set up clarifying questions for client to see immediately
            self.clarification_questions = result.questions or []

            # If there's a suggestion from the initial query, set it up
            # so the frontend sees "suggesting_result" status immediately
            if result.has_suggestion and result.suggestion_data:
                matched_id = self.research_manager.existing_result_id
                self.suggested_result_id = matched_id
                self.suggested_result_title = self.research_manager.existing_result_title
                self.suggested_result_summary = result.suggestion_data.short_summary
                self.suggested_result_image = self.research_manager.existing_image_path
                self.suggested_result_data = result.suggestion_data
                self.suggestion_accepted = None
                workflow.logger.info(
                    f"💡 Suggestion ready from initial query: {self.suggested_result_title}"
                )

            # Publish clarifications_generated event
            await workflow.execute_activity(
                publish_workflow_event,
                args=[
                    "clarifications_generated",
                    workflow.info().workflow_id,
                    {
                        "questions": self.clarification_questions,
                        "count": len(self.clarification_questions),
                    },
                ],
                start_to_close_timeout=timedelta(seconds=10),
            )
        else:
            # No clarifications needed, store the research data but let main loop complete it
            if result.report_data is not None:
                self.report_data = result.report_data
            # If research failed, main loop will handle fallback

        # Mark research as initialized so main loop can proceed
        self.research_initialized = True

        return self.get_status()

    @workflow.update
    async def provide_single_clarification(
        self, input: SingleClarificationInput
    ) -> ResearchInteractionDict:
        """Provide a single clarification response"""
        current_question = self._get_current_question()

        # Process clarification in activity
        result = await workflow.execute_activity(
            process_clarification,
            ProcessClarificationInput(
                answer=input.answer,
                current_question_index=self.current_question_index,
                current_question=current_question,
                total_questions=len(self.clarification_questions),
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Apply result to workflow state
        self.clarification_responses[result.question_key] = result.answer
        self.current_question_index = result.new_index

        # Publish clarification_answered event
        await workflow.execute_activity(
            publish_workflow_event,
            args=[
                "clarification_answered",
                workflow.info().workflow_id,
                {
                    "question_index": input.question_index,
                    "question": current_question,
                    "answer": input.answer,
                    "answers_collected": len(self.clarification_responses),
                    "total_questions": len(self.clarification_questions),
                },
            ],
            start_to_close_timeout=timedelta(seconds=10),
        )

        return self.get_status()

    @workflow.update
    async def provide_clarifications(
        self, input: ClarificationInput
    ) -> ResearchInteractionDict:
        """Provide all clarification responses at once (legacy compatibility)"""
        workflow.logger.info(
            f"Received {len(input.responses)} clarification responses: {input.responses}"
        )

        self.clarification_responses = input.responses
        # Mark all questions as answered
        self.current_question_index = len(self.clarification_questions)

        return self.get_status()

    @provide_single_clarification.validator
    def validate_single_clarification(self, input: SingleClarificationInput) -> None:
        if not input.answer.strip():
            raise ValueError("Answer cannot be empty")

        if not self.original_query:
            raise ValueError("No active research interaction")

        if self.early_match_found or self.research_completed:
            raise ValueError("Research already completed")

        if self.suggested_result_id and self.suggestion_accepted is None:
            raise ValueError("A suggestion is pending — accept or reject it first")

        if not self.clarification_questions or len(self.clarification_responses) >= len(
            self.clarification_questions
        ):
            raise ValueError("Not collecting clarifications")

    @provide_clarifications.validator
    def validate_provide_clarifications(self, input: ClarificationInput) -> None:
        if not input.responses:
            raise ValueError("Clarification responses cannot be empty")

        if not self.original_query:
            raise ValueError("No active research interaction")

        if not self.clarification_questions:
            raise ValueError("Not awaiting clarifications")

    @workflow.update
    async def accept_suggestion(self) -> ResearchInteractionDict:
        """Accept a suggested existing result"""
        workflow.logger.info(f"👍 User accepted suggestion: {self.suggested_result_id}")
        self.suggestion_accepted = True
        return self.get_status()

    @accept_suggestion.validator
    def validate_accept_suggestion(self) -> None:
        if not self.suggested_result_id:
            raise ValueError("No suggestion pending")
        if self.suggestion_accepted is not None:
            raise ValueError("Suggestion already responded to")

    @workflow.update
    async def reject_suggestion(self) -> ResearchInteractionDict:
        """Reject a suggested existing result"""
        workflow.logger.info(f"👎 User rejected suggestion: {self.suggested_result_id}")
        self.suggestion_accepted = False
        return self.get_status()

    @reject_suggestion.validator
    def validate_reject_suggestion(self) -> None:
        if not self.suggested_result_id:
            raise ValueError("No suggestion pending")
        if self.suggestion_accepted is not None:
            raise ValueError("Suggestion already responded to")

    @workflow.signal
    async def end_workflow_signal(self) -> None:
        """Signal to end the workflow"""
        self.workflow_ended = True
