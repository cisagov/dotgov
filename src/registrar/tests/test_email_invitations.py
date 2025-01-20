import unittest
from unittest.mock import patch, MagicMock
from datetime import date
from registrar.utility.email import EmailSendingError
from registrar.utility.email_invitations import send_domain_invitation_email

from api.tests.common import less_console_noise_decorator


class DomainInvitationEmail(unittest.TestCase):

    @less_console_noise_decorator
    @patch("registrar.utility.email_invitations.send_templated_email")
    @patch("registrar.utility.email_invitations.UserDomainRole.objects.filter")
    @patch("registrar.utility.email_invitations.validate_invitation")
    @patch("registrar.utility.email_invitations.get_requestor_email")
    @patch("registrar.utility.email_invitations.send_invitation_email")
    @patch("registrar.utility.email_invitations.normalize_domains")
    def test_send_domain_invitation_email(
        self,
        mock_normalize_domains,
        mock_send_invitation_email,
        mock_get_requestor_email,
        mock_validate_invitation,
        mock_user_domain_role_filter,
        mock_send_templated_email,
    ):
        """Test sending domain invitation email for one domain.
        Should also send emails to manager of that domain.
        """
        # Setup
        mock_domain = MagicMock(name="domain1")
        mock_domain.name = "example.com"
        mock_normalize_domains.return_value = [mock_domain]

        mock_requestor = MagicMock()
        mock_requestor_email = "requestor@example.com"
        mock_get_requestor_email.return_value = mock_requestor_email

        mock_user1 = MagicMock()
        mock_user1.email = "manager1@example.com"

        mock_user_domain_role_filter.return_value = [MagicMock(user=mock_user1)]

        email = "invitee@example.com"
        is_member_of_different_org = False

        # Call the function
        send_domain_invitation_email(
            email=email,
            requestor=mock_requestor,
            domains=mock_domain,
            is_member_of_different_org=is_member_of_different_org,
        )

        # Assertions
        mock_normalize_domains.assert_called_once_with(mock_domain)
        mock_get_requestor_email.assert_called_once_with(mock_requestor, [mock_domain])
        mock_validate_invitation.assert_called_once_with(
            email, [mock_domain], mock_requestor, is_member_of_different_org
        )
        mock_send_invitation_email.assert_called_once_with(email, mock_requestor_email, [mock_domain], None)
        mock_user_domain_role_filter.assert_called_once_with(domain=mock_domain)
        mock_send_templated_email.assert_called_once_with(
            "emails/domain_manager_notification.txt",
            "emails/domain_manager_notification_subject.txt",
            to_address=mock_user1.email,
            context={
                "domain": mock_domain,
                "requestor_email": mock_requestor_email,
                "invited_email_address": email,
                "domain_manager": mock_user1,
                "date": date.today(),
            },
        )

    @less_console_noise_decorator
    @patch("registrar.utility.email_invitations.send_templated_email")
    @patch("registrar.utility.email_invitations.UserDomainRole.objects.filter")
    @patch("registrar.utility.email_invitations.validate_invitation")
    @patch("registrar.utility.email_invitations.get_requestor_email")
    @patch("registrar.utility.email_invitations.send_invitation_email")
    @patch("registrar.utility.email_invitations.normalize_domains")
    def test_send_domain_invitation_email_multiple_domains(
        self,
        mock_normalize_domains,
        mock_send_invitation_email,
        mock_get_requestor_email,
        mock_validate_invitation,
        mock_user_domain_role_filter,
        mock_send_templated_email,
    ):
        """Test sending domain invitation email for multiple domains.
        Should also send emails to managers of each domain.
        """
        # Setup
        # Create multiple mock domains
        mock_domain1 = MagicMock(name="domain1")
        mock_domain1.name = "example.com"
        mock_domain2 = MagicMock(name="domain2")
        mock_domain2.name = "example.org"

        mock_normalize_domains.return_value = [mock_domain1, mock_domain2]

        mock_requestor = MagicMock()
        mock_requestor_email = "requestor@example.com"
        mock_get_requestor_email.return_value = mock_requestor_email

        mock_user1 = MagicMock()
        mock_user1.email = "manager1@example.com"
        mock_user2 = MagicMock()
        mock_user2.email = "manager2@example.com"

        # Configure domain roles for each domain
        def filter_side_effect(domain):
            if domain == mock_domain1:
                return [MagicMock(user=mock_user1)]
            elif domain == mock_domain2:
                return [MagicMock(user=mock_user2)]
            return []

        mock_user_domain_role_filter.side_effect = filter_side_effect

        email = "invitee@example.com"
        is_member_of_different_org = False

        # Call the function
        send_domain_invitation_email(
            email=email,
            requestor=mock_requestor,
            domains=[mock_domain1, mock_domain2],
            is_member_of_different_org=is_member_of_different_org,
        )

        # Assertions
        mock_normalize_domains.assert_called_once_with([mock_domain1, mock_domain2])
        mock_get_requestor_email.assert_called_once_with(mock_requestor, [mock_domain1, mock_domain2])
        mock_validate_invitation.assert_called_once_with(
            email, [mock_domain1, mock_domain2], mock_requestor, is_member_of_different_org
        )
        mock_send_invitation_email.assert_called_once_with(
            email, mock_requestor_email, [mock_domain1, mock_domain2], None
        )

        # Check that domain manager emails were sent for both domains
        mock_user_domain_role_filter.assert_any_call(domain=mock_domain1)
        mock_user_domain_role_filter.assert_any_call(domain=mock_domain2)

        mock_send_templated_email.assert_any_call(
            "emails/domain_manager_notification.txt",
            "emails/domain_manager_notification_subject.txt",
            to_address=mock_user1.email,
            context={
                "domain": mock_domain1,
                "requestor_email": mock_requestor_email,
                "invited_email_address": email,
                "domain_manager": mock_user1,
                "date": date.today(),
            },
        )
        mock_send_templated_email.assert_any_call(
            "emails/domain_manager_notification.txt",
            "emails/domain_manager_notification_subject.txt",
            to_address=mock_user2.email,
            context={
                "domain": mock_domain2,
                "requestor_email": mock_requestor_email,
                "invited_email_address": email,
                "domain_manager": mock_user2,
                "date": date.today(),
            },
        )

        # Verify the total number of calls to send_templated_email
        self.assertEqual(mock_send_templated_email.call_count, 2)

    @less_console_noise_decorator
    @patch("registrar.utility.email_invitations.validate_invitation")
    def test_send_domain_invitation_email_raises_invite_validation_exception(self, mock_validate_invitation):
        """Test sending domain invitation email for one domain and assert exception
        when invite validation fails.
        """
        # Setup
        mock_validate_invitation.side_effect = ValueError("Validation failed")
        email = "invitee@example.com"
        requestor = MagicMock()
        domain = MagicMock()

        # Call and assert exception
        with self.assertRaises(ValueError) as context:
            send_domain_invitation_email(email, requestor, domain, is_member_of_different_org=False)

        self.assertEqual(str(context.exception), "Validation failed")
        mock_validate_invitation.assert_called_once()

    @less_console_noise_decorator
    @patch("registrar.utility.email_invitations.get_requestor_email")
    def test_send_domain_invitation_email_raises_get_requestor_email_exception(self, mock_get_requestor_email):
        """Test sending domain invitation email for one domain and assert exception
        when get_requestor_email fails.
        """
        # Setup
        mock_get_requestor_email.side_effect = ValueError("Validation failed")
        email = "invitee@example.com"
        requestor = MagicMock()
        domain = MagicMock()

        # Call and assert exception
        with self.assertRaises(ValueError) as context:
            send_domain_invitation_email(email, requestor, domain, is_member_of_different_org=False)

        self.assertEqual(str(context.exception), "Validation failed")
        mock_get_requestor_email.assert_called_once()

    @less_console_noise_decorator
    @patch("registrar.utility.email_invitations.validate_invitation")
    @patch("registrar.utility.email_invitations.get_requestor_email")
    @patch("registrar.utility.email_invitations.send_invitation_email")
    @patch("registrar.utility.email_invitations.normalize_domains")
    def test_send_domain_invitation_email_raises_sending_email_exception(
        self,
        mock_normalize_domains,
        mock_send_invitation_email,
        mock_get_requestor_email,
        mock_validate_invitation,
    ):
        """Test sending domain invitation email for one domain and assert exception
        when send_invitation_email fails.
        """
        # Setup
        mock_domain = MagicMock(name="domain1")
        mock_domain.name = "example.com"
        mock_normalize_domains.return_value = [mock_domain]

        mock_requestor = MagicMock()
        mock_requestor_email = "requestor@example.com"
        mock_get_requestor_email.return_value = mock_requestor_email

        mock_user1 = MagicMock()
        mock_user1.email = "manager1@example.com"

        email = "invitee@example.com"
        is_member_of_different_org = False

        mock_send_invitation_email.side_effect = EmailSendingError("Error sending email")

        # Call and assert exception
        with self.assertRaises(EmailSendingError) as context:
            send_domain_invitation_email(
                email=email,
                requestor=mock_requestor,
                domains=mock_domain,
                is_member_of_different_org=is_member_of_different_org,
            )

        # Assertions
        mock_normalize_domains.assert_called_once_with(mock_domain)
        mock_get_requestor_email.assert_called_once_with(mock_requestor, [mock_domain])
        mock_validate_invitation.assert_called_once_with(
            email, [mock_domain], mock_requestor, is_member_of_different_org
        )
        self.assertEqual(str(context.exception), "Error sending email")

    @less_console_noise_decorator
    @patch("registrar.utility.email_invitations.send_emails_to_domain_managers")
    @patch("registrar.utility.email_invitations.validate_invitation")
    @patch("registrar.utility.email_invitations.get_requestor_email")
    @patch("registrar.utility.email_invitations.send_invitation_email")
    @patch("registrar.utility.email_invitations.normalize_domains")
    def test_send_domain_invitation_email_manager_emails_send_mail_exception(
        self,
        mock_normalize_domains,
        mock_send_invitation_email,
        mock_get_requestor_email,
        mock_validate_invitation,
        mock_send_domain_manager_emails,
    ):
        """Test sending domain invitation email for one domain and assert exception
        when send_emails_to_domain_managers fails.
        """
        # Setup
        mock_domain = MagicMock(name="domain1")
        mock_domain.name = "example.com"
        mock_normalize_domains.return_value = [mock_domain]

        mock_requestor = MagicMock()
        mock_requestor_email = "requestor@example.com"
        mock_get_requestor_email.return_value = mock_requestor_email

        email = "invitee@example.com"
        is_member_of_different_org = False

        mock_send_domain_manager_emails.side_effect = EmailSendingError("Error sending email")

        # Call and assert exception
        with self.assertRaises(EmailSendingError) as context:
            send_domain_invitation_email(
                email=email,
                requestor=mock_requestor,
                domains=mock_domain,
                is_member_of_different_org=is_member_of_different_org,
            )

        # Assertions
        mock_normalize_domains.assert_called_once_with(mock_domain)
        mock_get_requestor_email.assert_called_once_with(mock_requestor, [mock_domain])
        mock_validate_invitation.assert_called_once_with(
            email, [mock_domain], mock_requestor, is_member_of_different_org
        )
        mock_send_invitation_email.assert_called_once_with(email, mock_requestor_email, [mock_domain], None)
        self.assertEqual(str(context.exception), "Error sending email")
