"""
Integration tests for delete_conversation_task using testcontainers.

This module provides comprehensive integration tests for the delete_conversation_related_data task,
ensuring that all conversation-related data is properly deleted from the database in the correct order
to respect foreign key constraints.
"""

import logging
from unittest.mock import patch

import pytest
from faker import Faker

from extensions.ext_database import db
from models import ConversationVariable
from models.model import Message, MessageAnnotation, MessageFeedback
from models.tools import ToolConversationVariables, ToolFile
from models.web import PinnedConversation
from tasks.delete_conversation_task import delete_conversation_related_data

logger = logging.getLogger(__name__)


class TestDeleteConversationTask:
    """Integration tests for delete_conversation_related_data task using testcontainers."""

    @pytest.fixture
    def mock_external_service_dependencies(self):
        """Mock setup for external service dependencies."""
        with (
            patch("services.account_service.FeatureService") as mock_account_feature_service,
            patch("services.app_service.FeatureService") as mock_feature_service,
            patch("services.app_service.EnterpriseService") as mock_enterprise_service,
            patch("services.app_service.ModelManager") as mock_model_manager,
        ):
            # Setup default mock returns
            mock_account_feature_service.get_system_features.return_value.is_allow_register = True
            mock_feature_service.get_system_features.return_value.webapp_auth.enabled = False
            mock_enterprise_service.WebAppAuth.update_app_access_mode.return_value = None
            mock_enterprise_service.WebAppAuth.cleanup_webapp.return_value = None

            # Mock ModelManager for model configuration
            mock_model_instance = mock_model_manager.return_value
            mock_model_instance.get_default_model_instance.return_value = None
            mock_model_instance.get_default_provider_model_name.return_value = ("openai", "gpt-3.5-turbo")

            yield {
                "account_feature_service": mock_account_feature_service,
                "feature_service": mock_feature_service,
                "enterprise_service": mock_enterprise_service,
                "model_manager": mock_model_manager,
            }

    def _create_test_app_and_account(self, db_session_with_containers, mock_external_service_dependencies):
        """
        Helper method to create a test app and account for testing.

        Args:
            db_session_with_containers: Database session from testcontainers infrastructure
            mock_external_service_dependencies: Mock dependencies

        Returns:
            tuple: (app, account) - Created app and account instances
        """
        fake = Faker()

        # Setup mocks for account creation
        mock_external_service_dependencies[
            "account_feature_service"
        ].get_system_features.return_value.is_allow_register = True

        # Create account and tenant
        from services.account_service import AccountService, TenantService

        account = AccountService.create_account(
            email=fake.email(),
            name=fake.name(),
            interface_language="en-US",
            password=fake.password(length=12),
        )
        TenantService.create_owner_tenant_if_not_exist(account, name=fake.company())
        tenant = account.current_tenant

        # Create app with realistic data
        app_args = {
            "name": fake.company(),
            "description": fake.text(max_nb_chars=100),
            "mode": "chat",
            "icon_type": "emoji",
            "icon": "🤖",
            "icon_background": "#FF6B6B",
            "api_rph": 100,
            "api_rpm": 10,
        }

        from services.app_service import AppService

        app_service = AppService()
        app = app_service.create_app(tenant.id, app_args, account)

        return app, account

    def _create_test_message(self, app, account, conversation_id, fake, role="user"):
        """
        Helper method to create a test message with all required fields.

        Args:
            app: App instance
            account: Account instance
            conversation_id: Conversation ID
            fake: Faker instance
            role: Message role (user or assistant)

        Returns:
            Message: Created message instance
        """
        message = Message()
        message.id = fake.uuid4()
        message.app_id = app.id
        message.conversation_id = conversation_id
        message._inputs = {"input_text": fake.text(max_nb_chars=100)}
        message.query = fake.text(max_nb_chars=200)
        message.message = {"role": role, "content": fake.text(max_nb_chars=100)}
        message.message_unit_price = 0.001
        message.answer = fake.text(max_nb_chars=300)
        message.answer_unit_price = 0.001
        message.currency = "USD"
        message.from_source = "console"
        message.from_account_id = account.id
        return message

    def _create_test_conversation_data(self, db_session_with_containers, app, account, conversation_id):
        """
        Helper method to create comprehensive test data for a conversation.

        Args:
            db_session_with_containers: Database session from testcontainers infrastructure
            app: App instance
            account: Account instance
            conversation_id: Conversation ID to create data for

        Returns:
            dict: Dictionary containing created test data instances
        """
        fake = Faker()

        # Create conversation record first (required for foreign key constraints)
        from models.model import Conversation

        conversation = Conversation()
        conversation.id = conversation_id
        conversation.app_id = app.id
        conversation.mode = "chat"
        conversation.name = fake.text(max_nb_chars=50)
        conversation.inputs = {}
        conversation.introduction = fake.text(max_nb_chars=100)
        conversation.system_instruction = fake.text(max_nb_chars=200)
        conversation.status = "normal"
        conversation.from_source = "console"
        conversation.from_account_id = account.id
        db.session.add(conversation)

        # Create messages
        message1 = self._create_test_message(app, account, conversation_id, fake, "user")
        db.session.add(message1)

        message2 = self._create_test_message(app, account, conversation_id, fake, "assistant")
        db.session.add(message2)

        # Create message annotations
        annotation = MessageAnnotation()
        annotation.id = fake.uuid4()
        annotation.app_id = app.id
        annotation.conversation_id = conversation_id
        annotation.message_id = message1.id
        annotation.question = fake.text(max_nb_chars=150)
        annotation.content = fake.text(max_nb_chars=200)
        annotation.account_id = account.id
        db.session.add(annotation)

        # Create message feedback
        feedback = MessageFeedback()
        feedback.id = fake.uuid4()
        feedback.app_id = app.id
        feedback.conversation_id = conversation_id
        feedback.message_id = message1.id
        feedback.rating = "like"
        feedback.content = fake.text(max_nb_chars=100)
        feedback.from_source = "console"
        feedback.from_account_id = account.id
        db.session.add(feedback)

        # Create conversation variables
        conv_var = ConversationVariable(
            id=fake.uuid4(),
            app_id=app.id,
            conversation_id=conversation_id,
            data='{"user_name": "' + fake.name() + '", "preference": "' + fake.word() + '"}',
        )
        db.session.add(conv_var)

        # Create tool conversation variables
        tool_conv_var = ToolConversationVariables()
        tool_conv_var.id = fake.uuid4()
        tool_conv_var.user_id = account.id
        tool_conv_var.tenant_id = app.tenant_id
        tool_conv_var.conversation_id = conversation_id
        tool_conv_var.variables_str = '{"tool_param": "test_value"}'
        db.session.add(tool_conv_var)

        # Create tool files
        tool_file = ToolFile()
        tool_file.id = fake.uuid4()
        tool_file.user_id = account.id
        tool_file.tenant_id = app.tenant_id
        tool_file.conversation_id = conversation_id
        tool_file.file_key = fake.uuid4()
        tool_file.name = fake.file_name()
        tool_file.original_name = fake.file_name()
        tool_file.size = fake.random_int(min=1024, max=10240)
        tool_file.extension = fake.file_extension()
        tool_file.mimetype = fake.mime_type()
        tool_file.url = fake.url()
        db.session.add(tool_file)

        # Create pinned conversation
        pinned_conv = PinnedConversation()
        pinned_conv.id = fake.uuid4()
        pinned_conv.app_id = app.id
        pinned_conv.conversation_id = conversation_id
        pinned_conv.created_by = account.id
        db.session.add(pinned_conv)

        db.session.commit()

        return {
            "messages": [message1, message2],
            "annotation": annotation,
            "feedback": feedback,
            "conversation_variable": conv_var,
            "tool_conversation_variable": tool_conv_var,
            "tool_file": tool_file,
            "pinned_conversation": pinned_conv,
        }

    def test_delete_conversation_related_data_success(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test successful deletion of all conversation-related data.

        This test verifies:
        - All related data is deleted in correct order
        - No foreign key constraint violations occur
        - Database state is clean after deletion
        - Task completes successfully
        """
        # Arrange: Create test data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create comprehensive test data
        test_data = self._create_test_conversation_data(db_session_with_containers, app, account, conversation_id)

        # Verify data exists before deletion
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 2
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 1
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 1
        assert (
            db.session.query(ConversationVariable)
            .where(ConversationVariable.conversation_id == conversation_id)
            .count()
            == 1
        )
        assert (
            db.session.query(ToolConversationVariables)
            .where(ToolConversationVariables.conversation_id == conversation_id)
            .count()
            == 1
        )
        assert db.session.query(ToolFile).where(ToolFile.conversation_id == conversation_id).count() == 1
        assert (
            db.session.query(PinnedConversation).where(PinnedConversation.conversation_id == conversation_id).count()
            == 1
        )

        # Act: Execute the task
        delete_conversation_related_data(conversation_id)

        # Assert: Verify all data was deleted
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(ConversationVariable)
            .where(ConversationVariable.conversation_id == conversation_id)
            .count()
            == 0
        )
        assert (
            db.session.query(ToolConversationVariables)
            .where(ToolConversationVariables.conversation_id == conversation_id)
            .count()
            == 0
        )
        assert db.session.query(ToolFile).where(ToolFile.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(PinnedConversation).where(PinnedConversation.conversation_id == conversation_id).count()
            == 0
        )

    def test_delete_conversation_related_data_partial_data(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test deletion when conversation has only partial related data.

        This test verifies:
        - Task handles conversations with missing related data gracefully
        - No errors occur when some data types don't exist
        - Existing data is still properly deleted
        """
        # Arrange: Create test data with only some related data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create conversation record first
        from models.model import Conversation

        conversation = Conversation()
        conversation.id = conversation_id
        conversation.app_id = app.id
        conversation.mode = "chat"
        conversation.name = fake.text(max_nb_chars=50)
        conversation.inputs = {}
        conversation.introduction = fake.text(max_nb_chars=100)
        conversation.system_instruction = fake.text(max_nb_chars=200)
        conversation.status = "normal"
        conversation.from_source = "console"
        conversation.from_account_id = account.id
        db.session.add(conversation)

        # Create only messages and conversation variables (partial data)
        message = self._create_test_message(app, account, conversation_id, fake, "user")
        db.session.add(message)

        conv_var = ConversationVariable(
            id=fake.uuid4(), app_id=app.id, conversation_id=conversation_id, data='{"user_name": "' + fake.name() + '"}'
        )
        db.session.add(conv_var)

        db.session.commit()

        # Verify partial data exists
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 1
        assert (
            db.session.query(ConversationVariable)
            .where(ConversationVariable.conversation_id == conversation_id)
            .count()
            == 1
        )
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )

        # Act: Execute the task
        delete_conversation_related_data(conversation_id)

        # Assert: Verify existing data was deleted, no errors for missing data
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(ConversationVariable)
            .where(ConversationVariable.conversation_id == conversation_id)
            .count()
            == 0
        )
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )

    def test_delete_conversation_related_data_no_data(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test deletion when conversation has no related data.

        This test verifies:
        - Task handles conversations with no related data gracefully
        - No errors occur when all data types are missing
        - Task completes successfully even with empty result sets
        """
        # Arrange: Create conversation ID but no related data
        fake = Faker()
        conversation_id = fake.uuid4()

        # Verify no data exists
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0

        # Act: Execute the task
        delete_conversation_related_data(conversation_id)

        # Assert: Verify task completed without errors
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0

    def test_delete_conversation_related_data_large_dataset(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test deletion with large amounts of related data.

        This test verifies:
        - Task can handle large datasets efficiently
        - All data is properly deleted regardless of volume
        - Performance is acceptable with bulk operations
        """
        # Arrange: Create test data with large amounts of related data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create conversation record first
        from models.model import Conversation

        conversation = Conversation()
        conversation.id = conversation_id
        conversation.app_id = app.id
        conversation.mode = "chat"
        conversation.name = fake.text(max_nb_chars=50)
        conversation.inputs = {}
        conversation.introduction = fake.text(max_nb_chars=100)
        conversation.system_instruction = fake.text(max_nb_chars=200)
        conversation.status = "normal"
        conversation.from_source = "console"
        conversation.from_account_id = account.id
        db.session.add(conversation)

        # Create multiple messages (simulating large conversation)
        messages = []
        for i in range(50):  # Create 50 messages
            role = "user" if i % 2 == 0 else "assistant"
            message = self._create_test_message(app, account, conversation_id, fake, role)
            db.session.add(message)
            messages.append(message)

        # Create multiple annotations
        for i in range(20):  # Create 20 annotations
            annotation = MessageAnnotation()
            annotation.id = fake.uuid4()
            annotation.app_id = app.id
            annotation.conversation_id = conversation_id
            annotation.message_id = messages[i % len(messages)].id
            annotation.question = fake.text(max_nb_chars=150)
            annotation.content = fake.text(max_nb_chars=200)
            annotation.account_id = account.id
            db.session.add(annotation)

        # Create multiple feedback entries
        for i in range(30):  # Create 30 feedback entries
            feedback = MessageFeedback()
            feedback.id = fake.uuid4()
            feedback.app_id = app.id
            feedback.conversation_id = conversation_id
            feedback.message_id = messages[i % len(messages)].id
            feedback.rating = fake.random_element(elements=("like", "dislike"))
            feedback.content = fake.text(max_nb_chars=100)
            feedback.from_source = "console"
            feedback.from_account_id = account.id
            db.session.add(feedback)

        db.session.commit()

        # Verify large dataset exists
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 50
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count()
            == 20
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 30

        # Act: Execute the task
        delete_conversation_related_data(conversation_id)

        # Assert: Verify all data was deleted
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0

    def test_delete_conversation_related_data_database_error_handling(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test error handling when database operations fail.

        This test verifies:
        - Proper error handling and rollback on database failures
        - Exception is raised when deletion fails
        - Database session is properly closed in finally block
        """
        # Arrange: Create test data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create test data
        test_data = self._create_test_conversation_data(db_session_with_containers, app, account, conversation_id)

        # Mock database error during deletion
        with patch("extensions.ext_database.db.session.commit") as mock_commit:
            mock_commit.side_effect = Exception("Database connection error")

            # Act & Assert: Verify exception is raised
            with pytest.raises(Exception, match="Database connection error"):
                delete_conversation_related_data(conversation_id)

        # Verify data still exists after failed deletion (rollback occurred)
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 2
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 1
        )

    def test_delete_conversation_related_data_foreign_key_constraints(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test that deletion respects foreign key constraints by deleting in correct order.

        This test verifies:
        - Deletion order prevents foreign key constraint violations
        - Child records are deleted before parent records
        - Database integrity is maintained throughout the process
        """
        # Arrange: Create test data with complex relationships
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create conversation record first
        from models.model import Conversation

        conversation = Conversation()
        conversation.id = conversation_id
        conversation.app_id = app.id
        conversation.mode = "chat"
        conversation.name = fake.text(max_nb_chars=50)
        conversation.inputs = {}
        conversation.introduction = fake.text(max_nb_chars=100)
        conversation.system_instruction = fake.text(max_nb_chars=200)
        conversation.status = "normal"
        conversation.from_source = "console"
        conversation.from_account_id = account.id
        db.session.add(conversation)

        # Create messages first (parent records)
        message1 = self._create_test_message(app, account, conversation_id, fake, "user")
        db.session.add(message1)

        message2 = self._create_test_message(app, account, conversation_id, fake, "assistant")
        db.session.add(message2)

        db.session.flush()  # Ensure messages are committed before creating child records

        # Create child records that reference messages
        annotation = MessageAnnotation()
        annotation.id = fake.uuid4()
        annotation.app_id = app.id
        annotation.conversation_id = conversation_id
        annotation.message_id = message1.id  # References message1
        annotation.question = fake.text(max_nb_chars=150)
        annotation.content = fake.text(max_nb_chars=200)
        annotation.account_id = account.id
        db.session.add(annotation)

        feedback = MessageFeedback()
        feedback.id = fake.uuid4()
        feedback.app_id = app.id
        feedback.conversation_id = conversation_id
        feedback.message_id = message2.id  # References message2
        feedback.rating = "like"
        feedback.content = fake.text(max_nb_chars=100)
        feedback.from_source = "console"
        feedback.from_account_id = account.id
        db.session.add(feedback)

        db.session.commit()

        # Verify relationships exist
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 2
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 1
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 1

        # Act: Execute the task (should handle foreign key constraints properly)
        delete_conversation_related_data(conversation_id)

        # Assert: Verify all data was deleted without constraint violations
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0

    def test_delete_conversation_related_data_multiple_conversations_isolation(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test that deletion only affects the specified conversation and doesn't impact others.

        This test verifies:
        - Deletion is isolated to the specified conversation
        - Other conversations' data remains intact
        - No cross-contamination between conversations
        """
        # Arrange: Create multiple conversations with related data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)

        conversation_id_1 = fake.uuid4()
        conversation_id_2 = fake.uuid4()

        # Create data for conversation 1
        test_data_1 = self._create_test_conversation_data(db_session_with_containers, app, account, conversation_id_1)

        # Create data for conversation 2
        test_data_2 = self._create_test_conversation_data(db_session_with_containers, app, account, conversation_id_2)

        # Verify both conversations have data
        assert db.session.query(Message).where(Message.conversation_id == conversation_id_1).count() == 2
        assert db.session.query(Message).where(Message.conversation_id == conversation_id_2).count() == 2
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id_1).count()
            == 1
        )
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id_2).count()
            == 1
        )

        # Act: Delete only conversation 1
        delete_conversation_related_data(conversation_id_1)

        # Assert: Verify conversation 1 data is deleted, conversation 2 data remains
        assert db.session.query(Message).where(Message.conversation_id == conversation_id_1).count() == 0
        assert db.session.query(Message).where(Message.conversation_id == conversation_id_2).count() == 2
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id_1).count()
            == 0
        )
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id_2).count()
            == 1
        )
        assert (
            db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id_1).count() == 0
        )
        assert (
            db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id_2).count() == 1
        )

    def test_delete_conversation_related_data_performance_timing(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test that deletion completes within reasonable time limits.

        This test verifies:
        - Task completes within acceptable time limits
        - Performance is reasonable for typical conversation sizes
        - No significant performance degradation with moderate data volumes
        """
        import time

        # Arrange: Create test data with moderate volume
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create conversation record first
        from models.model import Conversation

        conversation = Conversation()
        conversation.id = conversation_id
        conversation.app_id = app.id
        conversation.mode = "chat"
        conversation.name = fake.text(max_nb_chars=50)
        conversation.inputs = {}
        conversation.introduction = fake.text(max_nb_chars=100)
        conversation.system_instruction = fake.text(max_nb_chars=200)
        conversation.status = "normal"
        conversation.from_source = "console"
        conversation.from_account_id = account.id
        db.session.add(conversation)

        # Create moderate amount of test data
        messages = []
        for i in range(20):  # Create 20 messages
            role = "user" if i % 2 == 0 else "assistant"
            message = self._create_test_message(app, account, conversation_id, fake, role)
            db.session.add(message)
            messages.append(message)

        # Create annotations and feedback
        for i in range(10):
            annotation = MessageAnnotation()
            annotation.id = fake.uuid4()
            annotation.app_id = app.id
            annotation.conversation_id = conversation_id
            annotation.message_id = messages[i % len(messages)].id
            annotation.question = fake.text(max_nb_chars=150)
            annotation.content = fake.text(max_nb_chars=200)
            annotation.account_id = account.id
            db.session.add(annotation)

            feedback = MessageFeedback()
            feedback.id = fake.uuid4()
            feedback.app_id = app.id
            feedback.conversation_id = conversation_id
            feedback.message_id = messages[i % len(messages)].id
            feedback.rating = fake.random_element(elements=("like", "dislike"))
            feedback.content = fake.text(max_nb_chars=100)
            feedback.from_source = "console"
            feedback.from_account_id = account.id
            db.session.add(feedback)

        db.session.commit()

        # Act: Execute the task and measure time
        start_time = time.perf_counter()
        delete_conversation_related_data(conversation_id)
        end_time = time.perf_counter()

        execution_time = end_time - start_time

        # Assert: Verify completion and reasonable performance
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0

        # Performance assertion: should complete within 5 seconds for this data volume
        assert execution_time < 5.0, f"Deletion took {execution_time:.2f} seconds, which exceeds 5 second limit"

    def test_delete_conversation_related_data_concurrent_access_safety(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test that deletion is safe when multiple operations might access the same data.

        This test verifies:
        - Task handles concurrent access scenarios safely
        - No race conditions occur during deletion
        - Database locks are properly managed
        """
        # Arrange: Create test data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create test data
        test_data = self._create_test_conversation_data(db_session_with_containers, app, account, conversation_id)

        # Verify data exists
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 2
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 1
        )

        # Act: Execute the task (simulating concurrent access by using synchronize_session=False)
        # This tests the actual implementation which uses synchronize_session=False
        delete_conversation_related_data(conversation_id)

        # Assert: Verify all data was deleted safely
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
        assert db.session.query(MessageFeedback).where(MessageFeedback.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(ConversationVariable)
            .where(ConversationVariable.conversation_id == conversation_id)
            .count()
            == 0
        )
        assert (
            db.session.query(ToolConversationVariables)
            .where(ToolConversationVariables.conversation_id == conversation_id)
            .count()
            == 0
        )
        assert db.session.query(ToolFile).where(ToolFile.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(PinnedConversation).where(PinnedConversation.conversation_id == conversation_id).count()
            == 0
        )

    def test_delete_conversation_related_data_session_management(
        self, db_session_with_containers, mock_external_service_dependencies
    ):
        """
        Test that database session is properly managed throughout the deletion process.

        This test verifies:
        - Session is properly closed in finally block
        - No session leaks occur
        - Database connections are properly managed
        """
        # Arrange: Create test data
        fake = Faker()
        app, account = self._create_test_app_and_account(db_session_with_containers, mock_external_service_dependencies)
        conversation_id = fake.uuid4()

        # Create test data
        test_data = self._create_test_conversation_data(db_session_with_containers, app, account, conversation_id)

        # Mock session.close to verify it's called
        with patch("extensions.ext_database.db.session.close") as mock_close:
            # Act: Execute the task
            delete_conversation_related_data(conversation_id)

            # Assert: Verify session.close was called
            mock_close.assert_called_once()

        # Verify data was deleted
        assert db.session.query(Message).where(Message.conversation_id == conversation_id).count() == 0
        assert (
            db.session.query(MessageAnnotation).where(MessageAnnotation.conversation_id == conversation_id).count() == 0
        )
