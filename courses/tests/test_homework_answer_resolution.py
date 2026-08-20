import base64
import json
import uuid

from django.test import TestCase, override_settings

from courses.homework_answer_checks import is_answer_correct
from courses.homework_answer_crypto import (
    HomeworkAnswerKeyring,
    encrypt_choice_answer,
    encrypt_scalar_answer,
)
from courses.homework_answer_resolution import resolve_correct_answer
from courses.models import Cohort, Course, Homework, Question, QuestionTypes


class HomeworkAnswerResolutionTests(TestCase):
    def setUp(self):
        self.keyring = HomeworkAnswerKeyring(
            active_key_id="test",
            keys={"test": b"t" * 32},
        )
        self.keyring_serialized = json.dumps(
            {
                "active_key_id": "test",
                "keys": {"test": base64.b64encode(b"t" * 32).decode("ascii")},
            }
        )
        self.course = Course.objects.create(slug="llm-zoomcamp", title="LLM Zoomcamp")
        self.cohort = Cohort.objects.create(
            slug="llm-zoomcamp-2026",
            identifier="2026",
            course=self.course,
            year=2026,
            title="LLM Zoomcamp 2026",
            description="Test cohort",
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="hw1",
            title="Homework 1",
            due_date="2026-08-20T12:00:00Z",
        )

    def source_identity(self):
        return {
            "source_content_id": uuid.uuid4(),
            "source_path": "cohorts/2026/01-agentic-rag/homework.yaml",
            "source_commit_sha": "a" * 40,
            "source_checksum": "b" * 64,
        }

    def test_legacy_questions_keep_plaintext_behavior(self):
        question = Question.objects.create(
            homework=self.homework,
            text="What is the answer?",
            question_type=QuestionTypes.FREE_FORM.value,
            correct_answer="legacy",
        )

        self.assertEqual(resolve_correct_answer(question, keyring=self.keyring), "legacy")
        self.assertEqual(question.get_correct_answer(), "legacy")

    def test_encrypted_free_form_answers_resolve_to_existing_string_contract(self):
        question_id = "implementation-language"
        question = Question.objects.create(
            homework=self.homework,
            text="Which language?",
            question_type=QuestionTypes.FREE_FORM.value,
            answer_type="EXS",
            **self.source_identity(),
            source_question_id=question_id,
            answer_envelope=encrypt_scalar_answer(
                "Python",
                course_slug="llm-zoomcamp",
                homework_slug="hw1",
                question_id=question_id,
                keyring=self.keyring,
            ),
        )

        self.assertEqual(resolve_correct_answer(question, keyring=self.keyring), "Python")

    def test_encrypted_choice_answers_resolve_to_one_based_indices(self):
        question_id = "best-language"
        question = Question.objects.create(
            homework=self.homework,
            text="Choose languages.",
            question_type=QuestionTypes.CHECKBOXES.value,
            **self.source_identity(),
            source_question_id=question_id,
            source_option_ids=["python", "go", "rust"],
            possible_answers="Python\nGo\nRust",
            answer_envelope=encrypt_choice_answer(
                ["python", "rust"],
                course_slug="llm-zoomcamp",
                homework_slug="hw1",
                question_id=question_id,
                keyring=self.keyring,
            ),
        )

        self.assertEqual(resolve_correct_answer(question, keyring=self.keyring), "1,3")
        answer = type("AnswerStub", (), {"answer_text": "1,3"})()
        with override_settings(COURSE_HOMEWORK_ANSWER_KEYRING=self.keyring_serialized):
            self.assertEqual(question.get_correct_answer_indices(), {1, 3})
            self.assertTrue(is_answer_correct(question, answer))

    @override_settings(COURSE_HOMEWORK_ANSWER_KEYRING="")
    def test_runtime_without_a_key_fails_closed(self):
        question_id = "missing-key"
        question = Question.objects.create(
            homework=self.homework,
            text="Protected",
            question_type=QuestionTypes.FREE_FORM.value,
            **self.source_identity(),
            source_question_id=question_id,
            answer_envelope=encrypt_scalar_answer(
                "secret",
                course_slug="llm-zoomcamp",
                homework_slug="hw1",
                question_id=question_id,
                keyring=self.keyring,
            ),
        )
        answer = type("AnswerStub", (), {"answer_text": "secret"})()

        self.assertFalse(is_answer_correct(question, answer))

    def test_configured_keyring_is_used_by_existing_scoring_path(self):
        question_id = "score-me"
        question = Question.objects.create(
            homework=self.homework,
            text="What is the answer?",
            question_type=QuestionTypes.FREE_FORM.value,
            answer_type="EXS",
            **self.source_identity(),
            source_question_id=question_id,
            answer_envelope=encrypt_scalar_answer(
                "correct",
                course_slug="llm-zoomcamp",
                homework_slug="hw1",
                question_id=question_id,
                keyring=self.keyring,
            ),
        )
        answer = type("AnswerStub", (), {"answer_text": "correct"})()

        with override_settings(COURSE_HOMEWORK_ANSWER_KEYRING=self.keyring_serialized):
            self.assertTrue(is_answer_correct(question, answer))
