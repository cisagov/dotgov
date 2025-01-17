from datetime import datetime
from django.forms import ValidationError
from django.utils import timezone
from waffle.testutils import override_flag
import re
from django.test import RequestFactory, Client, TestCase, override_settings
from django.contrib.admin.sites import AdminSite
from contextlib import ExitStack
from api.tests.common import less_console_noise_decorator
from django.contrib import messages
from django.urls import reverse
from registrar.admin import (
    DomainRequestAdmin,
    DomainRequestAdminForm,
    MyUserAdmin,
    AuditedAdmin,
)
from registrar.models import (
    Domain,
    DomainRequest,
    DomainInformation,
    DraftDomain,
    User,
    Contact,
    Website,
    SeniorOfficial,
    Portfolio,
    AllowedEmail,
    Suborganization,
)
from registrar.models.host import Host
from registrar.models.public_contact import PublicContact
from .common import (
    MockSESClient,
    completed_domain_request,
    generic_domain_object,
    less_console_noise,
    create_superuser,
    create_user,
    multiple_unalphabetical_domain_objects,
    MockEppLib,
    GenericTestHelper,
    normalize_html,
)
from unittest.mock import ANY, patch

from django.conf import settings
import boto3_mocking  # type: ignore
import logging

logger = logging.getLogger(__name__)


@boto3_mocking.patching
class TestDomainRequestAdmin(MockEppLib):
    """Test DomainRequestAdmin class as either staff or super user.

    Notes:
      all tests share superuser/staffuser; do not change these models in tests
      tests have available staffuser, superuser, client, admin and test_helper
    """

    @classmethod
    def setUpClass(self):
        super().setUpClass()
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.admin = DomainRequestAdmin(model=DomainRequest, admin_site=self.site)
        self.superuser = create_superuser()
        self.staffuser = create_user()
        self.client = Client(HTTP_HOST="localhost:8080")
        self.test_helper = GenericTestHelper(
            factory=self.factory,
            user=self.superuser,
            admin=self.admin,
            url="/admin/registrar/domainrequest/",
            model=DomainRequest,
        )
        self.mock_client = MockSESClient()
        allowed_emails = [AllowedEmail(email="mayor@igorville.gov"), AllowedEmail(email="help@get.gov")]
        AllowedEmail.objects.bulk_create(allowed_emails)

    def tearDown(self):
        super().tearDown()
        Host.objects.all().delete()
        PublicContact.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainRequest.objects.all().delete()
        Contact.objects.all().delete()
        Website.objects.all().delete()
        SeniorOfficial.objects.all().delete()
        Suborganization.objects.all().delete()
        Portfolio.objects.all().delete()
        self.mock_client.EMAILS_SENT.clear()

    @classmethod
    def tearDownClass(self):
        super().tearDownClass()
        User.objects.all().delete()
        AllowedEmail.objects.all().delete()

    @override_flag("organization_feature", active=True)
    @less_console_noise_decorator
    def test_clean_validates_duplicate_suborganization(self):
        """Tests that clean() prevents duplicate suborganization names within the same portfolio"""
        # Create a portfolio and existing suborganization
        portfolio = Portfolio.objects.create(organization_name="Test Portfolio", creator=self.superuser)

        # Create an existing suborganization
        Suborganization.objects.create(name="Existing Suborg", portfolio=portfolio)

        # Create a domain request trying to use the same suborganization name
        # (intentionally lowercase)
        domain_request = completed_domain_request(
            name="test1234.gov",
            portfolio=portfolio,
            requested_suborganization="existing suborg",
            suborganization_city="Rome",
            suborganization_state_territory=DomainRequest.StateTerritoryChoices.OHIO,
        )

        # Assert that the validation error is raised
        with self.assertRaises(ValidationError) as err:
            domain_request.clean()

        self.assertIn("This suborganization already exists", str(err.exception))

        # Test that a different name is allowed. Should not raise a error.
        domain_request.requested_suborganization = "New Suborg"
        domain_request.clean()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_clean_validates_partial_suborganization_fields(self):
        """Tests that clean() enforces all-or-nothing rule for suborganization fields"""
        portfolio = Portfolio.objects.create(organization_name="Test Portfolio", creator=self.superuser)

        # Create domain request with only city filled out
        domain_request = completed_domain_request(
            name="test1234.gov",
            portfolio=portfolio,
            suborganization_city="Test City",
        )

        # Assert validation error is raised with correct missing fields
        with self.assertRaises(ValidationError) as err:
            domain_request.clean()

        error_dict = err.exception.error_dict
        expected_missing = ["requested_suborganization", "suborganization_state_territory"]

        # Verify correct fields are flagged as required
        self.assertEqual(sorted(error_dict.keys()), sorted(expected_missing))

        # Verify error message
        for field in expected_missing:
            self.assertEqual(
                str(error_dict[field][0].message), "This field is required when creating a new suborganization."
            )

        # When all data is passed in, this should validate correctly
        domain_request.requested_suborganization = "Complete Suborg"
        domain_request.suborganization_state_territory = DomainRequest.StateTerritoryChoices.OHIO
        # Assert that no ValidationError is raised
        try:
            domain_request.clean()
        except ValidationError as e:
            self.fail(f"ValidationError was raised unexpectedly: {e}")

        # Also ensure that no validation error is raised if nothing is passed in at all
        domain_request.suborganization_city = None
        domain_request.requested_suborganization = None
        domain_request.suborganization_state_territory = None
        try:
            domain_request.clean()
        except ValidationError as e:
            self.fail(f"ValidationError was raised unexpectedly: {e}")

    @less_console_noise_decorator
    def test_domain_request_senior_official_is_alphabetically_sorted(self):
        """Tests if the senior offical dropdown is alphanetically sorted in the django admin display"""

        SeniorOfficial.objects.get_or_create(first_name="mary", last_name="joe", title="some other guy")
        SeniorOfficial.objects.get_or_create(first_name="alex", last_name="smoe", title="some guy")
        SeniorOfficial.objects.get_or_create(first_name="Zoup", last_name="Soup", title="title")

        contact, _ = Contact.objects.get_or_create(first_name="Henry", last_name="McFakerson")
        domain_request = completed_domain_request(name="city1.gov")
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))
        model_admin = AuditedAdmin(DomainRequest, self.site)

        # Get the queryset that would be returned for the list
        senior_offical_queryset = model_admin.formfield_for_foreignkey(
            DomainInformation.senior_official.field, request
        ).queryset

        # Make the list we're comparing on a bit prettier display-wise. Optional step.
        current_sort_order = []
        for official in senior_offical_queryset:
            current_sort_order.append(f"{official.first_name} {official.last_name}")

        expected_sort_order = ["alex smoe", "mary joe", "Zoup Soup"]

        self.assertEqual(current_sort_order, expected_sort_order)

    @less_console_noise_decorator
    def test_has_model_description(self):
        """Tests if this model has a model description on the table view"""
        self.client.force_login(self.superuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/",
            follow=True,
        )

        # Make sure that the page is loaded correctly
        self.assertEqual(response.status_code, 200)

        # Test for a description snippet
        self.assertContains(response, "This table contains all domain requests")
        self.assertContains(response, "Show more")

    @less_console_noise_decorator
    def test_helper_text(self):
        """
        Tests for the correct helper text on this page
        """

        # Create a fake domain request and domain
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        self.client.force_login(self.superuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # These should exist in the response
        expected_values = [
            ("creator", "Person who submitted the domain request. Will receive email updates"),
            ("approved_domain", "Domain associated with this request; will be blank until request is approved"),
            ("no_other_contacts_rationale", "Required if creator does not list other employees"),
            ("alternative_domains", "Other domain names the creator provided for consideration"),
            ("no_other_contacts_rationale", "Required if creator does not list other employees"),
            ("Urbanization", "Required for Puerto Rico only"),
        ]
        self.test_helper.assert_response_contains_distinct_values(response, expected_values)

    @less_console_noise_decorator
    def test_status_logs(self):
        """
        Tests that the status changes are shown in a table on the domain request change form,
        accurately and in chronological order.
        """

        def assert_status_count(normalized_content, status, count):
            """Helper function to assert the count of a status in the HTML content."""
            self.assertEqual(normalized_content.count(f"<td> {status} </td>"), count)

        def assert_status_order(normalized_content, statuses):
            """Helper function to assert the order of statuses in the HTML content."""
            start_index = 0
            for status in statuses:
                index = normalized_content.find(f"<td> {status} </td>", start_index)
                self.assertNotEqual(index, -1, f"Status '{status}' not found in the expected order.")
                start_index = index + len(status)

        # Create a fake domain request and domain
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.STARTED)

        self.client.force_login(self.superuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        domain_request.submit()
        domain_request.save()

        domain_request.in_review()
        domain_request.save()

        domain_request.action_needed()
        domain_request.action_needed_reason = DomainRequest.ActionNeededReasons.ALREADY_HAS_A_DOMAIN
        domain_request.save()

        # Let's just change the action needed reason
        domain_request.action_needed_reason = DomainRequest.ActionNeededReasons.ELIGIBILITY_UNCLEAR
        domain_request.save()

        domain_request.reject()
        domain_request.rejection_reason = DomainRequest.RejectionReasons.DOMAIN_PURPOSE
        domain_request.save()

        domain_request.in_review()
        domain_request.save()

        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Normalize the HTML response content
        normalized_content = " ".join(response.content.decode("utf-8").split())

        # Define the expected sequence of status changes
        expected_status_changes = [
            "In review",
            "Rejected - Purpose requirements not met",
            "Action needed - Unclear organization eligibility",
            "Action needed - Already has a domain",
            "In review",
            "Submitted",
            "Started",
        ]

        assert_status_order(normalized_content, expected_status_changes)

        assert_status_count(normalized_content, "Started", 1)
        assert_status_count(normalized_content, "Submitted", 1)
        assert_status_count(normalized_content, "In review", 2)
        assert_status_count(normalized_content, "Action needed - Already has a domain", 1)
        assert_status_count(normalized_content, "Action needed - Unclear organization eligibility", 1)
        assert_status_count(normalized_content, "Rejected - Purpose requirements not met", 1)

    @less_console_noise_decorator
    def test_collaspe_toggle_button_markup(self):
        """
        Tests for the correct collapse toggle button markup
        """

        # Create a fake domain request and domain
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        self.client.force_login(self.superuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)
        self.assertContains(response, "<span>Show details</span>")

    @less_console_noise_decorator
    def test_domain_requests_by_portfolio(self):
        """
        Tests that domain_requests display for a portfolio. And requests not in portfolio do not display.
        """

        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test Portfolio", creator=self.superuser)
        # Create a fake domain request and domain
        domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW, portfolio=portfolio
        )
        domain_request2 = completed_domain_request(
            name="testdomain2.gov", status=DomainRequest.DomainRequestStatus.IN_REVIEW
        )

        self.client.force_login(self.superuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/?portfolio={}".format(portfolio.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)
        self.assertNotContains(response, domain_request2.requested_domain.name)
        self.assertContains(response, portfolio.organization_name)

    @less_console_noise_decorator
    def test_analyst_can_see_and_edit_alternative_domain(self):
        """Tests if an analyst can still see and edit the alternative domain field"""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
        )

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)

        fake_website = Website.objects.create(website="thisisatest.gov")
        _domain_request.alternative_domains.add(fake_website)
        _domain_request.save()

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(_domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _domain_request.requested_domain.name)

        # Test if the page has the alternative domain
        self.assertContains(response, "thisisatest.gov")

        # Check that the page contains the url we expect
        expected_href = reverse("admin:registrar_website_change", args=[fake_website.id])
        self.assertContains(response, expected_href)

        # Navigate to the website to ensure that we can still edit it
        response = self.client.get(
            "/admin/registrar/website/{}/change/".format(fake_website.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "thisisatest.gov")

        # clean up objects in this test
        fake_website.delete()
        _domain_request.delete()
        _creator.delete()

    @less_console_noise_decorator
    def test_analyst_can_see_and_edit_requested_domain(self):
        """Tests if an analyst can still see and edit the requested domain field"""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
        )

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(_domain_request.pk),
            follow=True,
        )

        # Filter to get the latest from the DB (rather than direct assignment)
        requested_domain = DraftDomain.objects.filter(name=_domain_request.requested_domain.name).get()

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, requested_domain.name)

        # Check that the page contains the url we expect
        expected_href = reverse("admin:registrar_draftdomain_change", args=[requested_domain.id])
        self.assertContains(response, expected_href)

        # Navigate to the website to ensure that we can still edit it
        response = self.client.get(
            "/admin/registrar/draftdomain/{}/change/".format(requested_domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "city.gov")

        # clean up objects in this test
        _domain_request.delete()
        requested_domain.delete()
        _creator.delete()

    @less_console_noise_decorator
    def test_analyst_can_see_current_websites(self):
        """Tests if an analyst can still see current website field"""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
        )

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)

        fake_website = Website.objects.create(website="thisisatest.gov")
        _domain_request.current_websites.add(fake_website)
        _domain_request.save()

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(_domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _domain_request.requested_domain.name)

        # Test if the page has the current website
        self.assertContains(response, "thisisatest.gov")

        # clean up objects in this test
        fake_website.delete()
        _domain_request.delete()
        _creator.delete()

    @less_console_noise_decorator
    def test_domain_sortable(self):
        """Tests if the DomainRequest sorts by domain correctly"""
        self.client.force_login(self.superuser)

        multiple_unalphabetical_domain_objects("domain_request")

        # Assert that our sort works correctly
        self.test_helper.assert_table_sorted("1", ("requested_domain__name",))

        # Assert that sorting in reverse works correctly
        self.test_helper.assert_table_sorted("-1", ("-requested_domain__name",))

    @less_console_noise_decorator
    def test_creator_sortable(self):
        """Tests if the DomainRequest sorts by creator correctly"""
        self.client.force_login(self.superuser)

        multiple_unalphabetical_domain_objects("domain_request")

        additional_domain_request = generic_domain_object("domain_request", "Xylophone")
        new_user = User.objects.filter(username=additional_domain_request.investigator.username).get()
        new_user.first_name = "Xylophonic"
        new_user.save()

        # Assert that our sort works correctly
        self.test_helper.assert_table_sorted(
            "13",
            (
                "creator__first_name",
                "creator__last_name",
            ),
        )

        # Assert that sorting in reverse works correctly
        self.test_helper.assert_table_sorted(
            "-13",
            (
                "-creator__first_name",
                "-creator__last_name",
            ),
        )

        # clean up objects in this test
        new_user.delete()

    @less_console_noise_decorator
    def test_investigator_sortable(self):
        """Tests if the DomainRequest sorts by investigator correctly"""
        self.client.force_login(self.superuser)

        multiple_unalphabetical_domain_objects("domain_request")
        additional_domain_request = generic_domain_object("domain_request", "Xylophone")
        new_user = User.objects.filter(username=additional_domain_request.investigator.username).get()
        new_user.first_name = "Xylophonic"
        new_user.save()

        # Assert that our sort works correctly
        self.test_helper.assert_table_sorted(
            "14",
            (
                "investigator__first_name",
                "investigator__last_name",
            ),
        )

        # Assert that sorting in reverse works correctly
        self.test_helper.assert_table_sorted(
            "-14",
            (
                "-investigator__first_name",
                "-investigator__last_name",
            ),
        )

        # clean up objects in this test
        new_user.delete()

    @less_console_noise_decorator
    def test_default_sorting_in_domain_requests_list(self):
        """
        Make sure the default sortin in on the domain requests list page is reverse last_submitted_date
        then alphabetical requested_domain
        """

        # Create domain requests with different names
        domain_requests = [
            completed_domain_request(status=DomainRequest.DomainRequestStatus.SUBMITTED, name=name)
            for name in ["ccc.gov", "bbb.gov", "eee.gov", "aaa.gov", "zzz.gov", "ddd.gov"]
        ]

        domain_requests[0].last_submitted_date = timezone.make_aware(datetime(2024, 10, 16))
        domain_requests[1].last_submitted_date = timezone.make_aware(datetime(2001, 10, 16))
        domain_requests[2].last_submitted_date = timezone.make_aware(datetime(1980, 10, 16))
        domain_requests[3].last_submitted_date = timezone.make_aware(datetime(1998, 10, 16))
        domain_requests[4].last_submitted_date = timezone.make_aware(datetime(2013, 10, 16))
        domain_requests[5].last_submitted_date = timezone.make_aware(datetime(1980, 10, 16))

        # Save the modified domain requests to update their attributes in the database
        for domain_request in domain_requests:
            domain_request.save()

        # Refresh domain request objects from the database to reflect the changes
        domain_requests = [DomainRequest.objects.get(pk=domain_request.pk) for domain_request in domain_requests]

        # Login as superuser and retrieve the domain request list page
        self.client.force_login(self.superuser)
        response = self.client.get("/admin/registrar/domainrequest/")

        # Check that the response is successful
        self.assertEqual(response.status_code, 200)

        # Extract the domain names from the response content using regex
        domain_names_match = re.findall(r"(\w+\.gov)</a>", response.content.decode("utf-8"))

        logger.info(f"domain_names_match {domain_names_match}")

        # Verify that domain names are found
        self.assertTrue(domain_names_match)

        # Extract the domain names
        domain_names = [match for match in domain_names_match]

        # Verify that the domain names are displayed in the expected order
        expected_order = [
            "ccc.gov",
            "zzz.gov",
            "bbb.gov",
            "aaa.gov",
            "ddd.gov",
            "eee.gov",
        ]

        # Remove duplicates
        # Remove duplicates from domain_names list while preserving order
        unique_domain_names = []
        for domain_name in domain_names:
            if domain_name not in unique_domain_names:
                unique_domain_names.append(domain_name)

        self.assertEqual(unique_domain_names, expected_order)

    @less_console_noise_decorator
    def test_short_org_name_in_domain_requests_list(self):
        """
        Make sure the short name is displaying in admin on the list page
        """
        self.client.force_login(self.superuser)
        completed_domain_request()
        response = self.client.get("/admin/registrar/domainrequest/?generic_org_type__exact=federal")
        # There are 2 template references to Federal (4) and two in the results data
        # of the request
        self.assertContains(response, "Federal", count=52)
        self.assertContains(response, "federal", count=383)
        # This may be a bit more robust
        self.assertContains(response, '<td class="field-converted_generic_org_type">Federal</td>', count=1)
        # Now let's make sure the long description does not exist
        self.assertNotContains(response, "Federal: an agency of the U.S. government")

    @less_console_noise_decorator
    def test_default_status_in_domain_requests_list(self):
        """
        Make sure the default status in admin is selected on the domain requests list page
        """
        self.client.force_login(self.superuser)
        completed_domain_request()
        response = self.client.get("/admin/registrar/domainrequest/")
        # The results are filtered by "status in [submitted,in review,action needed]"
        self.assertContains(response, "status in [submitted,in review,action needed]", count=1)

    @less_console_noise_decorator
    def transition_state_and_send_email(
        self,
        domain_request,
        status,
        rejection_reason=None,
        action_needed_reason=None,
        action_needed_reason_email=None,
    ):
        """Helper method for the email test cases."""

        with boto3_mocking.clients.handler_for("sesv2", self.mock_client), ExitStack() as stack:
            stack.enter_context(patch.object(messages, "warning"))
            # Create a mock request
            request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))

            # Create a fake session to hook to
            request.session = {}

            # Modify the domain request's properties
            domain_request.status = status

            if rejection_reason:
                domain_request.rejection_reason = rejection_reason

            if action_needed_reason:
                domain_request.action_needed_reason = action_needed_reason

            if action_needed_reason_email:
                domain_request.action_needed_reason_email = action_needed_reason_email

            # Use the model admin's save_model method
            self.admin.save_model(request, domain_request, form=None, change=True)

    def assert_email_is_accurate(
        self, expected_string, email_index, email_address, test_that_no_bcc=False, bcc_email_address=""
    ):
        """Helper method for the email test cases.
        email_index is the index of the email in mock_client."""
        AllowedEmail.objects.get_or_create(email=email_address)
        AllowedEmail.objects.get_or_create(email=bcc_email_address)
        with less_console_noise():
            # Access the arguments passed to send_email
            call_args = self.mock_client.EMAILS_SENT
            kwargs = call_args[email_index]["kwargs"]

            # Retrieve the email details from the arguments
            from_email = kwargs.get("FromEmailAddress")
            to_email = kwargs["Destination"]["ToAddresses"][0]
            email_content = kwargs["Content"]
            email_body = email_content["Simple"]["Body"]["Text"]["Data"]

            # Assert or perform other checks on the email details
            self.assertEqual(from_email, settings.DEFAULT_FROM_EMAIL)
            self.assertEqual(to_email, email_address)
            self.assertIn(expected_string, email_body)

        if test_that_no_bcc:
            _ = ""
            with self.assertRaises(KeyError):
                with less_console_noise():
                    _ = kwargs["Destination"]["BccAddresses"][0]
            self.assertEqual(_, "")

        if bcc_email_address:
            bcc_email = kwargs["Destination"]["BccAddresses"][0]
            self.assertEqual(bcc_email, bcc_email_address)

    @override_settings(IS_PRODUCTION=True)
    @less_console_noise_decorator
    def test_action_needed_sends_reason_email_prod_bcc(self):
        """When an action needed reason is set, an email is sent out and help@get.gov
        is BCC'd in production"""
        # Create fake creator
        EMAIL = "meoward.jones@igorville.gov"

        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email=EMAIL,
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        BCC_EMAIL = settings.DEFAULT_FROM_EMAIL
        in_review = DomainRequest.DomainRequestStatus.IN_REVIEW
        action_needed = DomainRequest.DomainRequestStatus.ACTION_NEEDED

        # Create a sample domain request
        domain_request = completed_domain_request(status=in_review, user=_creator)

        # Test the email sent out for already_has_a_domain
        already_has_a_domain = DomainRequest.ActionNeededReasons.ALREADY_HAS_A_DOMAIN
        self.transition_state_and_send_email(domain_request, action_needed, action_needed_reason=already_has_a_domain)

        self.assert_email_is_accurate("ORGANIZATION ALREADY HAS A .GOV DOMAIN", 0, EMAIL, bcc_email_address=BCC_EMAIL)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # We use javascript to reset the content of this. It is only automatically set
        # if the email itself is somehow None.
        self._reset_action_needed_email(domain_request)

        # Test the email sent out for bad_name
        bad_name = DomainRequest.ActionNeededReasons.BAD_NAME
        self.transition_state_and_send_email(domain_request, action_needed, action_needed_reason=bad_name)
        self.assert_email_is_accurate(
            "DOMAIN NAME DOES NOT MEET .GOV REQUIREMENTS", 1, EMAIL, bcc_email_address=BCC_EMAIL
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)
        self._reset_action_needed_email(domain_request)

        # Test the email sent out for eligibility_unclear
        eligibility_unclear = DomainRequest.ActionNeededReasons.ELIGIBILITY_UNCLEAR
        self.transition_state_and_send_email(domain_request, action_needed, action_needed_reason=eligibility_unclear)
        self.assert_email_is_accurate(
            "ORGANIZATION MAY NOT MEET ELIGIBILITY REQUIREMENTS", 2, EMAIL, bcc_email_address=BCC_EMAIL
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)
        self._reset_action_needed_email(domain_request)

        # Test that a custom email is sent out for questionable_so
        questionable_so = DomainRequest.ActionNeededReasons.QUESTIONABLE_SENIOR_OFFICIAL
        self.transition_state_and_send_email(domain_request, action_needed, action_needed_reason=questionable_so)
        self.assert_email_is_accurate(
            "SENIOR OFFICIAL DOES NOT MEET ELIGIBILITY REQUIREMENTS", 3, _creator.email, bcc_email_address=BCC_EMAIL
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 4)
        self._reset_action_needed_email(domain_request)

        # Assert that no other emails are sent on OTHER
        other = DomainRequest.ActionNeededReasons.OTHER
        self.transition_state_and_send_email(domain_request, action_needed, action_needed_reason=other)

        # Should be unchanged from before
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 4)
        self._reset_action_needed_email(domain_request)

        # Tests if an analyst can override existing email content
        questionable_so = DomainRequest.ActionNeededReasons.QUESTIONABLE_SENIOR_OFFICIAL
        self.transition_state_and_send_email(
            domain_request,
            action_needed,
            action_needed_reason=questionable_so,
            action_needed_reason_email="custom email content",
        )

        domain_request.refresh_from_db()
        self.assert_email_is_accurate("custom email content", 4, _creator.email, bcc_email_address=BCC_EMAIL)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 5)
        self._reset_action_needed_email(domain_request)

        # Tests if a new email gets sent when just the email is changed.
        # An email should NOT be sent out if we just modify the email content.
        self.transition_state_and_send_email(
            domain_request,
            action_needed,
            action_needed_reason=questionable_so,
            action_needed_reason_email="dummy email content",
        )

        self.assertEqual(len(self.mock_client.EMAILS_SENT), 5)
        self._reset_action_needed_email(domain_request)

        # Set the request back to in review
        domain_request.in_review()

        # Try sending another email when changing states AND including content
        self.transition_state_and_send_email(
            domain_request,
            action_needed,
            action_needed_reason=eligibility_unclear,
            action_needed_reason_email="custom content when starting anew",
        )
        self.assert_email_is_accurate(
            "custom content when starting anew", 5, _creator.email, bcc_email_address=BCC_EMAIL
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 6)

    def _reset_action_needed_email(self, domain_request):
        """Sets the given action needed email back to none"""
        domain_request.action_needed_reason_email = None
        domain_request.save()
        domain_request.refresh_from_db()

    @override_settings(IS_PRODUCTION=True)
    @less_console_noise_decorator
    def test_rejected_sends_reason_email_prod_bcc(self):
        """When a rejection reason is set, an email is sent out and help@get.gov
        is BCC'd in production"""
        # Create fake creator
        EMAIL = "meoward.jones@igorville.gov"

        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email=EMAIL,
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        BCC_EMAIL = settings.DEFAULT_FROM_EMAIL
        in_review = DomainRequest.DomainRequestStatus.IN_REVIEW
        rejected = DomainRequest.DomainRequestStatus.REJECTED

        # Create a sample domain request
        domain_request = completed_domain_request(status=in_review, user=_creator)

        expected_emails = {
            DomainRequest.RejectionReasons.DOMAIN_PURPOSE: "You didn’t provide enough information about how",
            DomainRequest.RejectionReasons.REQUESTOR_NOT_ELIGIBLE: "You must be a government employee, or be",
            DomainRequest.RejectionReasons.ORG_HAS_DOMAIN: "practice is to approve one domain",
            DomainRequest.RejectionReasons.CONTACTS_NOT_VERIFIED: "we could not verify the organizational",
            DomainRequest.RejectionReasons.ORG_NOT_ELIGIBLE: ".Gov domains are only available to official U.S.-based",
            DomainRequest.RejectionReasons.NAMING_REQUIREMENTS: "does not meet our naming requirements",
            DomainRequest.RejectionReasons.OTHER: "YOU CAN SUBMIT A NEW REQUEST",
        }
        for i, (reason, email_content) in enumerate(expected_emails.items()):
            with self.subTest(reason=reason):
                self.transition_state_and_send_email(domain_request, status=rejected, rejection_reason=reason)
                self.assert_email_is_accurate(email_content, i, EMAIL, bcc_email_address=BCC_EMAIL)
                self.assertEqual(len(self.mock_client.EMAILS_SENT), i + 1)
            domain_request.rejection_reason_email = None
            domain_request.save()
            domain_request.refresh_from_db()

    @less_console_noise_decorator
    def test_save_model_sends_submitted_email(self):
        """When transitioning to submitted from started or withdrawn on a domain request,
        an email is sent out.

        When transitioning to submitted from dns needed or in review on a domain request,
        no email is sent out.

        Also test that the default email set in settings is NOT BCCd on non-prod whenever
        an email does go out."""

        EMAIL = "meoward.jones@igorville.gov"

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email=EMAIL,
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Test Submitted Status from started
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assert_email_is_accurate("We received your .gov domain request.", 0, _creator.email, True)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Test Withdrawn Status
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.WITHDRAWN)
        self.assert_email_is_accurate(
            "Your .gov domain request has been withdrawn and will not be reviewed by our team.", 1, _creator.email, True
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

        # Test Submitted Status Again (from withdrawn)
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Move it to IN_REVIEW
        other = DomainRequest.ActionNeededReasons.OTHER
        in_review = DomainRequest.DomainRequestStatus.IN_REVIEW
        self.transition_state_and_send_email(domain_request, in_review, action_needed_reason=other)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Test Submitted Status Again from in IN_REVIEW, no new email should be sent
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Move it to IN_REVIEW
        self.transition_state_and_send_email(domain_request, in_review, action_needed_reason=other)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Move it to ACTION_NEEDED
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.ACTION_NEEDED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Test Submitted Status Again from in ACTION_NEEDED, no new email should be sent
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

    @override_settings(IS_PRODUCTION=True)
    @less_console_noise_decorator
    def test_save_model_sends_submitted_email_with_bcc_on_prod(self):
        """When transitioning to submitted from started or withdrawn on a domain request,
        an email is sent out.

        When transitioning to submitted from dns needed or in review on a domain request,
        no email is sent out.

        Also test that the default email set in settings IS BCCd on prod whenever
        an email does go out."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        BCC_EMAIL = settings.DEFAULT_FROM_EMAIL

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Test Submitted Status from started
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assert_email_is_accurate("We received your .gov domain request.", 0, _creator.email, False, BCC_EMAIL)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Test Withdrawn Status
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.WITHDRAWN)
        self.assert_email_is_accurate(
            "Your .gov domain request has been withdrawn and will not be reviewed by our team.", 1, _creator.email
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

        # Test Submitted Status Again (from withdrawn)
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assert_email_is_accurate("We received your .gov domain request.", 0, _creator.email, False, BCC_EMAIL)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Move it to IN_REVIEW
        other = domain_request.ActionNeededReasons.OTHER
        in_review = DomainRequest.DomainRequestStatus.IN_REVIEW
        self.transition_state_and_send_email(domain_request, in_review, action_needed_reason=other)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Test Submitted Status Again from in IN_REVIEW, no new email should be sent
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Move it to IN_REVIEW
        self.transition_state_and_send_email(domain_request, in_review, action_needed_reason=other)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Move it to ACTION_NEEDED
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.ACTION_NEEDED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

        # Test Submitted Status Again from in ACTION_NEEDED, no new email should be sent
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

    @less_console_noise_decorator
    def test_save_model_sends_approved_email(self):
        """When transitioning to approved on a domain request,
        an email is sent out every time."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Test Submitted Status
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 0, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Test Withdrawn Status
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.DOMAIN_PURPOSE,
        )
        self.assert_email_is_accurate("Your .gov domain request has been rejected.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

        # Test Submitted Status Again (No new email should be sent)
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_purpose_not_met(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is domain purpose."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason DOMAIN_PURPOSE and test email
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.DOMAIN_PURPOSE,
        )
        self.assert_email_is_accurate(
            "Your domain request was rejected because the purpose you provided did not meet our \nrequirements.",
            0,
            _creator.email,
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_requestor(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is requestor."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason REQUESTOR and test email including dynamic organization name
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.REQUESTOR_NOT_ELIGIBLE,
        )
        self.assert_email_is_accurate(
            "Your domain request was rejected because we don’t believe you’re eligible to request a \n.gov "
            "domain on behalf of Testorg",
            0,
            _creator.email,
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_org_has_domain(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is second domain."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason SECOND_DOMAIN_REASONING and test email including dynamic organization name
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.ORG_HAS_DOMAIN,
        )
        self.assert_email_is_accurate(
            "Your domain request was rejected because Testorg has a .gov domain.", 0, _creator.email
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_contacts_or_org_legitimacy(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is contacts or org legitimacy."""
        # Create fake creator

        EMAIL = "meoward.jones@igorville.gov"
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email=EMAIL,
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason CONTACTS_OR_ORGANIZATION_LEGITIMACY and test email including dynamic organization name
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.CONTACTS_NOT_VERIFIED,
        )
        self.assert_email_is_accurate(
            "Your domain request was rejected because we could not verify the organizational \n"
            "contacts you provided. If you have questions or comments, reply to this email.",
            0,
            _creator.email,
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_org_eligibility(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is org eligibility."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason ORGANIZATION_ELIGIBILITY and test email including dynamic organization name
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.ORG_NOT_ELIGIBLE,
        )
        self.assert_email_is_accurate(
            "Your domain request was rejected because we determined that Testorg is not \neligible for "
            "a .gov domain.",
            0,
            _creator.email,
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_naming(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is naming."""
        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason NAMING_REQUIREMENTS and test email including dynamic organization name
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.NAMING_REQUIREMENTS,
        )
        self.assert_email_is_accurate(
            "Your domain request was rejected because it does not meet our naming requirements.", 0, _creator.email
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_save_model_sends_rejected_email_other(self):
        """When transitioning to rejected on a domain request, an email is sent
        explaining why when the reason is other."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelist user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Reject for reason NAMING_REQUIREMENTS and test email including dynamic organization name
        self.transition_state_and_send_email(
            domain_request,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.OTHER,
        )
        self.assert_email_is_accurate("Choosing a .gov domain name", 0, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Approve
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.APPROVED)
        self.assert_email_is_accurate("Congratulations! Your .gov domain request has been approved.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

    @less_console_noise_decorator
    def test_transition_to_rejected_without_rejection_reason_does_trigger_error(self):
        """
        When transitioning to rejected without a rejection reason, admin throws a user friendly message.

        The transition fails.
        """

        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.APPROVED)

        # Create a request object with a superuser
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))
        request.user = self.superuser

        with ExitStack() as stack:
            stack.enter_context(patch.object(messages, "error"))
            stack.enter_context(patch.object(messages, "warning"))
            domain_request.status = DomainRequest.DomainRequestStatus.REJECTED

            self.admin.save_model(request, domain_request, None, True)

            messages.error.assert_called_once_with(
                request,
                "A reason is required for this status.",
            )

        domain_request.refresh_from_db()
        self.assertEqual(domain_request.status, DomainRequest.DomainRequestStatus.APPROVED)

    @less_console_noise_decorator
    def test_transition_to_rejected_with_rejection_reason_does_not_trigger_error(self):
        """
        When transitioning to rejected with a rejection reason, admin does not throw an error alert.

        The transition is successful.
        """

        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.APPROVED)

        # Create a request object with a superuser
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))
        request.user = self.superuser

        with ExitStack() as stack:
            stack.enter_context(patch.object(messages, "error"))
            stack.enter_context(patch.object(messages, "warning"))
            domain_request.status = DomainRequest.DomainRequestStatus.REJECTED
            domain_request.rejection_reason = DomainRequest.RejectionReasons.CONTACTS_NOT_VERIFIED

            self.admin.save_model(request, domain_request, None, True)

            messages.error.assert_not_called()

        domain_request.refresh_from_db()
        self.assertEqual(domain_request.status, DomainRequest.DomainRequestStatus.REJECTED)

    @less_console_noise_decorator
    def test_save_model_sends_withdrawn_email(self):
        """When transitioning to withdrawn on a domain request,
        an email is sent out every time."""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a sample domain request and whitelists user email
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        AllowedEmail.objects.get_or_create(email=_creator.email)

        # Test Submitted Status
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.WITHDRAWN)
        self.assert_email_is_accurate(
            "Your .gov domain request has been withdrawn and will not be reviewed by our team.", 0, _creator.email
        )
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 1)

        # Test Withdrawn Status
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.SUBMITTED)
        self.assert_email_is_accurate("We received your .gov domain request.", 1, _creator.email)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 2)

        # Test Submitted Status Again (No new email should be sent)
        self.transition_state_and_send_email(domain_request, DomainRequest.DomainRequestStatus.WITHDRAWN)
        self.assertEqual(len(self.mock_client.EMAILS_SENT), 3)

    @less_console_noise_decorator
    def test_save_model_sets_approved_domain(self):
        # make sure there is no user with this email
        EMAIL = "mayor@igorville.gov"
        User.objects.filter(email=EMAIL).delete()

        # Create a sample domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        # Create a mock request
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))

        with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
            with ExitStack() as stack:
                stack.enter_context(patch.object(messages, "warning"))
                # Modify the domain request's property
                domain_request.status = DomainRequest.DomainRequestStatus.APPROVED

                # Use the model admin's save_model method
                self.admin.save_model(request, domain_request, form=None, change=True)

        # Test that approved domain exists and equals requested domain
        self.assertEqual(domain_request.requested_domain.name, domain_request.approved_domain.name)

    @less_console_noise_decorator
    def test_sticky_submit_row(self):
        """Test that the change_form template contains strings indicative of the customization
        of the sticky submit bar.

        Also test that it does NOT contain a CSS class meant for analysts only when logged in as superuser."""

        # make sure there is no user with this email
        EMAIL = "mayor@igorville.gov"
        User.objects.filter(email=EMAIL).delete()
        self.client.force_login(self.superuser)

        # Create a sample domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        # Create a mock request
        request = self.client.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))

        # Since we're using client to mock the request, we can only test against
        # non-interpolated values
        expected_content = "Requested domain:"
        expected_content2 = '<span class="scroll-indicator"></span>'
        expected_content3 = '<div class="submit-row-wrapper">'
        not_expected_content = "submit-row-wrapper--analyst-view>"
        self.assertContains(request, expected_content)
        self.assertContains(request, expected_content2)
        self.assertContains(request, expected_content3)
        self.assertNotContains(request, not_expected_content)

    @less_console_noise_decorator
    def test_sticky_submit_row_has_extra_class_for_analysts(self):
        """Test that the change_form template contains strings indicative of the customization
        of the sticky submit bar.

        Also test that it DOES contain a CSS class meant for analysts only when logged in as analyst."""

        # make sure there is no user with this email
        EMAIL = "mayor@igorville.gov"
        User.objects.filter(email=EMAIL).delete()
        self.client.force_login(self.staffuser)

        # Create a sample domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        # Create a mock request
        request = self.client.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))

        # Since we're using client to mock the request, we can only test against
        # non-interpolated values
        expected_content = "Requested domain:"
        expected_content2 = '<span class="scroll-indicator"></span>'
        expected_content3 = '<div class="submit-row-wrapper submit-row-wrapper--analyst-view">'
        self.assertContains(request, expected_content)
        self.assertContains(request, expected_content2)
        self.assertContains(request, expected_content3)

    @less_console_noise_decorator
    def test_other_contacts_has_readonly_link(self):
        """Tests if the readonly other_contacts field has links"""

        # Create a fake domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        # Get the other contact
        other_contact = domain_request.other_contacts.all().first()

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the page contains the url we expect
        expected_href = reverse("admin:registrar_contact_change", args=[other_contact.id])
        self.assertContains(response, expected_href)

        # Check that the page contains the link we expect.
        # Since the url is dynamic (populated by JS), we can test for its existence
        # by checking for the end tag.
        expected_url = "Testy Tester</a>"
        self.assertContains(response, expected_url)

    @less_console_noise_decorator
    def test_other_websites_has_one_readonly_link(self):
        """Tests if the readonly other_websites field has links.
        Test markup for one website."""

        # Create a fake domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the page contains the link we expect.
        expected_markup = """
            <p class="margin-y-0 padding-y-0">
                <a href="city.com" target="_blank">
                    city.com
                </a>
            </p>
            """

        normalized_expected = normalize_html(expected_markup)
        normalized_response = normalize_html(response.content.decode("utf-8"))

        index = normalized_response.find(normalized_expected)

        # Assert that the expected markup is found in the response
        if index == -1:
            self.fail(
                f"Expected markup not found in the response.\n\n"
                f"Expected:\n{normalized_expected}\n\n"
                f"Start index of mismatch: {index}\n\n"
                f"Consider checking the surrounding response for context."
            )

    @less_console_noise_decorator
    def test_other_websites_has_few_readonly_links(self):
        """Tests if the readonly other_websites field has links.
        Test markup for 5 or less websites."""

        # Create a domain request with 4 current websites
        domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW,
            current_websites=["city.gov", "city2.gov", "city3.gov", "city4.gov"],
        )

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the page contains the link we expect.
        expected_markup = """
            <ul class="margin-top-0 margin-left-0 padding-left-0">
                <li><a href="city.gov" target="_blank">city.gov</a></li>
                <li><a href="city2.gov" target="_blank">city2.gov</a></li>
                <li><a href="city3.gov" target="_blank">city3.gov</a></li>
                <li><a href="city4.gov" target="_blank">city4.gov</a></li>
            </ul>
            """

        normalized_expected = normalize_html(expected_markup)
        normalized_response = normalize_html(response.content.decode("utf-8"))

        index = normalized_response.find(normalized_expected)

        # Assert that the expected markup is found in the response
        if index == -1:
            self.fail(
                f"Expected markup not found in the response.\n\n"
                f"Expected:\n{normalized_expected}\n\n"
                f"Start index of mismatch: {index}\n\n"
                f"Consider checking the surrounding response for context."
            )

    @less_console_noise_decorator
    def test_other_websites_has_lots_readonly_links(self):
        """Tests if the readonly other_websites field has links.
        Test markup for 6 or more websites."""

        # Create a domain requests with 6 current websites
        domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW,
            current_websites=["city.gov", "city2.gov", "city3.gov", "city4.gov", "city5.gov", "city6.gov"],
        )

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the page contains the link we expect.
        expected_markup = """
            <ul class="margin-top-0 margin-left-0 padding-left-0 admin-list-inline">
                <li><a href="city.gov" target="_blank">city.gov</a></li>
                <li><a href="city2.gov" target="_blank">city2.gov</a></li>
                <li><a href="city3.gov" target="_blank">city3.gov</a></li>
                <li><a href="city4.gov" target="_blank">city4.gov</a></li>
                <li><a href="city5.gov" target="_blank">city5.gov</a></li>
                <li><a href="city6.gov" target="_blank">city6.gov</a></li>
            </ul>
            """

        normalized_expected = normalize_html(expected_markup)
        normalized_response = normalize_html(response.content.decode("utf-8"))

        index = normalized_response.find(normalized_expected)

        # Assert that the expected markup is found in the response
        if index == -1:
            self.fail(
                f"Expected markup not found in the response.\n\n"
                f"Expected:\n{normalized_expected}\n\n"
                f"Start index of mismatch: {index}\n\n"
                f"Consider checking the surrounding response for context."
            )

    @less_console_noise_decorator
    def test_alternative_domains_has_one_readonly_link(self):
        """Tests if the readonly alternative_domains field has links.
        Test markup for one website."""

        # Create a fake domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the page contains the link we expect.
        website = Website.objects.filter(website="city1.gov").first()
        base_url = "/admin/registrar/website"
        return_path = f"/admin/registrar/domainrequest/{domain_request.pk}/change/"
        expected_markup = f"""
            <p class="margin-y-0 padding-y-0">
            <a href="{base_url}/{website.pk}/change/?return_path={return_path}" target="_blank">city1.gov</a>
            </p>
        """

        normalized_expected = normalize_html(expected_markup)
        normalized_response = normalize_html(response.content.decode("utf-8"))

        index = normalized_response.find(normalized_expected)

        # Assert that the expected markup is found in the response
        if index == -1:
            self.fail(
                f"Expected markup not found in the response.\n\n"
                f"Expected:\n{normalized_expected}\n\n"
                f"Start index of mismatch: {index}\n\n"
                f"Consider checking the surrounding response for context."
            )

    @less_console_noise_decorator
    def test_alternative_domains_has_lots_readonly_link(self):
        """Tests if the readonly other_websites field has links.
        Test markup for 6 or more websites."""

        # Create a domain request with 6 alternative domains
        domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW,
            alternative_domains=[
                "altcity1.gov",
                "altcity2.gov",
                "altcity3.gov",
                "altcity4.gov",
                "altcity5.gov",
                "altcity6.gov",
            ],
        )

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the page contains the link we expect.
        website1 = Website.objects.filter(website="altcity1.gov").first()
        website2 = Website.objects.filter(website="altcity2.gov").first()
        website3 = Website.objects.filter(website="altcity3.gov").first()
        website4 = Website.objects.filter(website="altcity4.gov").first()
        website5 = Website.objects.filter(website="altcity5.gov").first()
        website6 = Website.objects.filter(website="altcity6.gov").first()
        base_url = "/admin/registrar/website"
        return_path = f"/admin/registrar/domainrequest/{domain_request.pk}/change/"
        attr = 'target="_blank"'
        expected_markup = f"""
            <ul class="margin-top-0 margin-left-0 padding-left-0 admin-list-inline">
                <li><a href="{base_url}/{website1.pk}/change/?return_path={return_path}" {attr}>altcity1.gov</a></li>
                <li><a href="{base_url}/{website2.pk}/change/?return_path={return_path}" {attr}>altcity2.gov</a></li>
                <li><a href="{base_url}/{website3.pk}/change/?return_path={return_path}" {attr}>altcity3.gov</a></li>
                <li><a href="{base_url}/{website4.pk}/change/?return_path={return_path}" {attr}>altcity4.gov</a></li>
                <li><a href="{base_url}/{website5.pk}/change/?return_path={return_path}" {attr}>altcity5.gov</a></li>
                <li><a href="{base_url}/{website6.pk}/change/?return_path={return_path}" {attr}>altcity6.gov</a></li>
            </ul>
            """

        normalized_expected = normalize_html(expected_markup)
        normalized_response = normalize_html(response.content.decode("utf-8"))

        index = normalized_response.find(normalized_expected)

        # Assert that the expected markup is found in the response
        if index == -1:
            self.fail(
                f"Expected markup not found in the response.\n\n"
                f"Expected:\n{normalized_expected}\n\n"
                f"Start index of mismatch: {index}\n\n"
                f"Consider checking the surrounding response for context."
            )

    @less_console_noise_decorator
    def test_contact_fields_have_detail_table(self):
        """Tests if the contact fields have the detail table which displays title, email, and phone"""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a fake domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # == Check for the creator == #

        # Check for the right title, email, and phone number in the response.
        expected_creator_fields = [
            # Field, expected value
            ("title", "Treat inspector"),
            ("phone", "(555) 123 12345"),
        ]
        self.test_helper.assert_response_contains_distinct_values(response, expected_creator_fields)
        self.assertContains(response, "meoward.jones@igorville.gov")

        # Check for the field itself
        self.assertContains(response, "Meoward Jones")

        # == Check for the senior_official == #
        self.assertContains(response, "testy@town.com", count=2)
        expected_so_fields = [
            # Field, expected value
            ("phone", "(555) 555 5555"),
        ]

        self.test_helper.assert_response_contains_distinct_values(response, expected_so_fields)
        self.assertContains(response, "Chief Tester")

        # == Test the other_employees field == #
        self.assertContains(response, "testy2@town.com")
        expected_other_employees_fields = [
            # Field, expected value
            ("title", "Another Tester"),
            ("phone", "(555) 555 5557"),
        ]
        self.test_helper.assert_response_contains_distinct_values(response, expected_other_employees_fields)

        # Test for the copy link
        # We expect 5 in the form + 2 from the js module copy-to-clipboard.js
        # that gets pulled in the test in django.contrib.staticfiles.finders.FileSystemFinder
        self.assertContains(response, "copy-to-clipboard", count=7)

        # Test that Creator counts display properly
        self.assertNotContains(response, "Approved domains")
        self.assertContains(response, "Active requests")

        # cleanup objects from this test
        domain_request.delete()
        _creator.delete()

    @less_console_noise_decorator
    def test_save_model_sets_restricted_status_on_user(self):
        # make sure there is no user with this email
        EMAIL = "mayor@igorville.gov"
        User.objects.filter(email=EMAIL).delete()

        # Create a sample domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        # Create a mock request
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk), follow=True)

        with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
            # Modify the domain request's property
            domain_request.status = DomainRequest.DomainRequestStatus.INELIGIBLE

            # Use the model admin's save_model method
            self.admin.save_model(request, domain_request, form=None, change=True)

        # Test that approved domain exists and equals requested domain
        self.assertEqual(domain_request.creator.status, "restricted")

    @less_console_noise_decorator
    def test_user_sets_restricted_status_modal(self):
        """Tests the modal for when a user sets the status to restricted"""
        # make sure there is no user with this email
        EMAIL = "mayor@igorville.gov"
        User.objects.filter(email=EMAIL).delete()

        # Create a sample domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # Check that the modal has the right content
        # Check for the header
        self.assertContains(response, "Are you sure you want to select ineligible status?")

        # Check for some of its body
        self.assertContains(response, "When a domain request is in ineligible status")

        # Check for some of the button content
        self.assertContains(response, "Yes, select ineligible status")

        # Create a mock request
        request = self.factory.post("/admin/registrar/domainrequest{}/change/".format(domain_request.pk), follow=True)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
            # Modify the domain request's property
            domain_request.status = DomainRequest.DomainRequestStatus.INELIGIBLE

            # Use the model admin's save_model method
            self.admin.save_model(request, domain_request, form=None, change=True)

        # Test that approved domain exists and equals requested domain
        self.assertEqual(domain_request.creator.status, "restricted")

        # 'Get' to the domain request again
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain_request.requested_domain.name)

        # The modal should be unchanged
        self.assertContains(response, "Are you sure you want to select ineligible status?")
        self.assertContains(response, "When a domain request is in ineligible status")
        self.assertContains(response, "Yes, select ineligible status")

    @less_console_noise_decorator
    def test_readonly_when_restricted_creator(self):
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
            domain_request.creator.status = User.RESTRICTED
            domain_request.creator.save()

        request = self.factory.get("/")
        request.user = self.superuser

        readonly_fields = self.admin.get_readonly_fields(request, domain_request)

        expected_fields = [
            "portfolio_senior_official",
            "portfolio_organization_type",
            "portfolio_federal_type",
            "portfolio_organization_name",
            "portfolio_federal_agency",
            "portfolio_state_territory",
            "portfolio_address_line1",
            "portfolio_address_line2",
            "portfolio_city",
            "portfolio_zipcode",
            "portfolio_urbanization",
            "other_contacts",
            "current_websites",
            "alternative_domains",
            "is_election_board",
            "status_history",
            "id",
            "created_at",
            "updated_at",
            "status",
            "rejection_reason",
            "rejection_reason_email",
            "action_needed_reason",
            "action_needed_reason_email",
            "federal_agency",
            "portfolio",
            "sub_organization",
            "requested_suborganization",
            "suborganization_city",
            "suborganization_state_territory",
            "creator",
            "investigator",
            "generic_org_type",
            "is_election_board",
            "organization_type",
            "federally_recognized_tribe",
            "state_recognized_tribe",
            "tribe_name",
            "federal_type",
            "organization_name",
            "address_line1",
            "address_line2",
            "city",
            "state_territory",
            "zipcode",
            "urbanization",
            "about_your_organization",
            "senior_official",
            "approved_domain",
            "requested_domain",
            "purpose",
            "no_other_contacts_rationale",
            "anything_else",
            "has_anything_else_text",
            "cisa_representative_email",
            "cisa_representative_first_name",
            "cisa_representative_last_name",
            "has_cisa_representative",
            "is_policy_acknowledged",
            "first_submitted_date",
            "last_submitted_date",
            "last_status_update",
            "notes",
            "alternative_domains",
        ]
        self.assertEqual(readonly_fields, expected_fields)

    def test_readonly_fields_for_analyst(self):
        with less_console_noise():
            request = self.factory.get("/")  # Use the correct method and path
            request.user = self.staffuser

            readonly_fields = self.admin.get_readonly_fields(request)
            expected_fields = [
                "portfolio_senior_official",
                "portfolio_organization_type",
                "portfolio_federal_type",
                "portfolio_organization_name",
                "portfolio_federal_agency",
                "portfolio_state_territory",
                "portfolio_address_line1",
                "portfolio_address_line2",
                "portfolio_city",
                "portfolio_zipcode",
                "portfolio_urbanization",
                "other_contacts",
                "current_websites",
                "alternative_domains",
                "is_election_board",
                "status_history",
                "federal_agency",
                "creator",
                "about_your_organization",
                "requested_domain",
                "approved_domain",
                "alternative_domains",
                "purpose",
                "no_other_contacts_rationale",
                "anything_else",
                "is_policy_acknowledged",
                "cisa_representative_first_name",
                "cisa_representative_last_name",
                "cisa_representative_email",
            ]
            self.assertEqual(readonly_fields, expected_fields)

    def test_readonly_fields_for_superuser(self):
        with less_console_noise():
            request = self.factory.get("/")  # Use the correct method and path
            request.user = self.superuser

            readonly_fields = self.admin.get_readonly_fields(request)

            expected_fields = [
                "portfolio_senior_official",
                "portfolio_organization_type",
                "portfolio_federal_type",
                "portfolio_organization_name",
                "portfolio_federal_agency",
                "portfolio_state_territory",
                "portfolio_address_line1",
                "portfolio_address_line2",
                "portfolio_city",
                "portfolio_zipcode",
                "portfolio_urbanization",
                "other_contacts",
                "current_websites",
                "alternative_domains",
                "is_election_board",
                "status_history",
            ]

            self.assertEqual(readonly_fields, expected_fields)

    def test_saving_when_restricted_creator(self):
        with less_console_noise():
            # Create an instance of the model
            domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)
            with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
                domain_request.creator.status = User.RESTRICTED
                domain_request.creator.save()

            # Create a request object with a superuser
            request = self.factory.get("/")
            request.user = self.superuser

            with patch("django.contrib.messages.error") as mock_error:
                # Simulate saving the model
                self.admin.save_model(request, domain_request, None, False)

                # Assert that the error message was called with the correct argument
                mock_error.assert_called_once_with(
                    request,
                    "This action is not permitted for domain requests with a restricted creator.",
                )

            # Assert that the status has not changed
            self.assertEqual(domain_request.status, DomainRequest.DomainRequestStatus.IN_REVIEW)

    def test_change_view_with_restricted_creator(self):
        with less_console_noise():
            # Create an instance of the model
            domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)
            with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
                domain_request.creator.status = User.RESTRICTED
                domain_request.creator.save()

            with patch("django.contrib.messages.warning") as mock_warning:
                # Create a request object with a superuser
                request = self.factory.get("/admin/your_app/domainrequest/{}/change/".format(domain_request.pk))
                request.user = self.superuser

                self.admin.display_restricted_warning(request, domain_request)

                # Assert that the error message was called with the correct argument
                mock_warning.assert_called_once_with(
                    request,
                    "Cannot edit a domain request with a restricted creator.",
                )

    @less_console_noise_decorator
    def test_approved_domain_request_with_ready_domain_has_warning_message(self):
        """Tests if the domain request has a warning message when the approved domain is in Ready state"""
        # Create an instance of the model
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)
        # Approve the domain request
        domain_request.approve()
        domain_request.save()

        # Add nameservers to get to Ready state
        domain_request.approved_domain.nameservers = [
            ("ns1.city.gov", ["1.1.1.1"]),
            ("ns2.city.gov", ["1.1.1.2"]),
        ]
        domain_request.approved_domain.save()

        with boto3_mocking.clients.handler_for("sesv2", self.mock_client):
            with patch("django.contrib.messages.warning") as mock_warning:
                # Create a request object
                self.client.force_login(self.superuser)
                self.client.get(
                    "/admin/registrar/domainrequest/{}/change/".format(domain_request.pk),
                    follow=True,
                )

                # Assert that the error message was called with the correct argument
                mock_warning.assert_called_once_with(
                    ANY,  # don't care about the request argument
                    f"The status of this domain request cannot be changed because it has been joined to a domain in Ready status: <a href='/admin/registrar/domain/{domain_request.approved_domain.id}/change/'>{domain_request.approved_domain.name}</a>",  # noqa
                )

    def trigger_saving_approved_to_another_state(self, domain_is_active, another_state, rejection_reason=None):
        """Helper method that triggers domain request state changes from approved to another state,
        with an associated domain that can be either active (READY) or not.

        Used to test errors when saving a change with an active domain, also used to test side effects
        when saving a change goes through."""

        with less_console_noise():
            # Create an instance of the model
            domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.APPROVED)
            domain = Domain.objects.create(name=domain_request.requested_domain.name)
            domain_information = DomainInformation.objects.create(creator=self.superuser, domain=domain)
            domain_request.approved_domain = domain
            domain_request.save()

            # Create a request object with a superuser
            request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))
            request.user = self.superuser

            request.session = {}

            # Define a custom implementation for is_active
            def custom_is_active(self):
                return domain_is_active  # Override to return True

            # Use ExitStack to combine patch contexts
            with ExitStack() as stack:
                # Patch Domain.is_active and django.contrib.messages.error simultaneously
                stack.enter_context(patch.object(Domain, "is_active", custom_is_active))
                stack.enter_context(patch.object(messages, "error"))
                stack.enter_context(patch.object(messages, "warning"))
                stack.enter_context(patch.object(messages, "success"))

                domain_request.status = another_state

                if another_state == DomainRequest.DomainRequestStatus.ACTION_NEEDED:
                    domain_request.action_needed_reason = domain_request.ActionNeededReasons.OTHER

                domain_request.rejection_reason = rejection_reason

                self.admin.save_model(request, domain_request, None, True)

                # Assert that the error message was called with the correct argument
                if domain_is_active:
                    messages.error.assert_called_once_with(
                        request,
                        "This action is not permitted. The domain " + "is already active.",
                    )
                else:
                    # Assert that the error message was never called
                    messages.error.assert_not_called()

                    self.assertEqual(domain_request.approved_domain, None)

                    # Assert that Domain got Deleted
                    with self.assertRaises(Domain.DoesNotExist):
                        domain.refresh_from_db()

                    # Assert that DomainInformation got Deleted
                    with self.assertRaises(DomainInformation.DoesNotExist):
                        domain_information.refresh_from_db()

    def test_error_when_saving_approved_to_in_review_and_domain_is_active(self):
        self.trigger_saving_approved_to_another_state(True, DomainRequest.DomainRequestStatus.IN_REVIEW)

    def test_error_when_saving_approved_to_action_needed_and_domain_is_active(self):
        self.trigger_saving_approved_to_another_state(True, DomainRequest.DomainRequestStatus.ACTION_NEEDED)

    def test_error_when_saving_approved_to_rejected_and_domain_is_active(self):
        self.trigger_saving_approved_to_another_state(True, DomainRequest.DomainRequestStatus.REJECTED)

    def test_error_when_saving_approved_to_ineligible_and_domain_is_active(self):
        self.trigger_saving_approved_to_another_state(True, DomainRequest.DomainRequestStatus.INELIGIBLE)

    def test_side_effects_when_saving_approved_to_in_review(self):
        self.trigger_saving_approved_to_another_state(False, DomainRequest.DomainRequestStatus.IN_REVIEW)

    def test_side_effects_when_saving_approved_to_action_needed(self):
        self.trigger_saving_approved_to_another_state(False, DomainRequest.DomainRequestStatus.ACTION_NEEDED)

    def test_side_effects_when_saving_approved_to_rejected(self):
        self.trigger_saving_approved_to_another_state(
            False,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.RejectionReasons.CONTACTS_NOT_VERIFIED,
        )

    def test_side_effects_when_saving_approved_to_ineligible(self):
        self.trigger_saving_approved_to_another_state(False, DomainRequest.DomainRequestStatus.INELIGIBLE)

    @less_console_noise
    def test_error_when_saving_to_approved_and_domain_exists(self):
        """Redundant admin check on model transition not allowed."""
        Domain.objects.create(name="wabbitseason.gov")

        new_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.SUBMITTED, name="wabbitseason.gov"
        )

        # Create a request object with a superuser
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(new_request.pk))
        request.user = self.superuser

        request.session = {}

        # Use ExitStack to combine patch contexts
        with ExitStack() as stack:
            # Patch django.contrib.messages.error
            stack.enter_context(patch.object(messages, "error"))

            new_request.status = DomainRequest.DomainRequestStatus.APPROVED

            self.admin.save_model(request, new_request, None, True)

            messages.error.assert_called_once_with(
                request,
                "Cannot approve. Requested domain is already in use.",
            )

    @less_console_noise
    def test_no_error_when_saving_to_approved_and_domain_exists(self):
        """The negative of the redundant admin check on model transition not allowed."""
        new_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.SUBMITTED)

        # Create a request object with a superuser
        request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(new_request.pk))
        request.user = self.superuser

        request.session = {}

        # Use ExitStack to combine patch contexts
        with ExitStack() as stack:
            # Patch Domain.is_active and django.contrib.messages.error simultaneously
            stack.enter_context(patch.object(messages, "error"))

            new_request.status = DomainRequest.DomainRequestStatus.APPROVED

            self.admin.save_model(request, new_request, None, True)

            # Assert that the error message was never called
            messages.error.assert_not_called()

    def test_has_correct_filters(self):
        """
        This test verifies that DomainRequestAdmin has the correct filters set up.

        It retrieves the current list of filters from DomainRequestAdmin
        and checks that it matches the expected list of filters.
        """
        with less_console_noise():
            request = self.factory.get("/")
            request.user = self.superuser

            # Grab the current list of table filters
            readonly_fields = self.admin.get_list_filter(request)
            expected_fields = (
                DomainRequestAdmin.PortfolioFilter,
                DomainRequestAdmin.StatusListFilter,
                DomainRequestAdmin.GenericOrgFilter,
                DomainRequestAdmin.FederalTypeFilter,
                DomainRequestAdmin.ElectionOfficeFilter,
                "rejection_reason",
                DomainRequestAdmin.InvestigatorFilter,
            )

            self.assertEqual(readonly_fields, expected_fields)

    def test_table_sorted_alphabetically(self):
        """
        This test verifies that the DomainRequestAdmin table is sorted alphabetically
        by the 'requested_domain__name' field.

        It creates a list of DomainRequest instances in a non-alphabetical order,
        then retrieves the queryset from the DomainRequestAdmin and checks
        that it matches the expected queryset,
        which is sorted alphabetically by the 'requested_domain__name' field.
        """
        with less_console_noise():
            # Creates a list of DomainRequests in scrambled order
            multiple_unalphabetical_domain_objects("domain_request")

            request = self.factory.get("/")
            request.user = self.superuser

            # Get the expected list of alphabetically sorted DomainRequests
            expected_order = DomainRequest.objects.order_by("requested_domain__name")

            # Get the returned queryset
            queryset = self.admin.get_queryset(request)

            # Check the order
            self.assertEqual(
                list(queryset),
                list(expected_order),
            )

    def test_displays_investigator_filter(self):
        """
        This test verifies that the investigator filter in the admin interface for
        the DomainRequest model displays correctly.

        It creates two DomainRequest instances, each with a different investigator.
        It then simulates a staff user logging in and applying the investigator filter
        on the DomainRequest admin page.

        We then test if the page displays the filter we expect, but we do not test
        if we get back the correct response in the table. This is to isolate if
        the filter displays correctly, when the filter isn't filtering correctly.
        """

        with less_console_noise():
            # Create a mock DomainRequest object, with a fake investigator
            domain_request: DomainRequest = generic_domain_object("domain_request", "SomeGuy")
            investigator_user = User.objects.filter(username=domain_request.investigator.username).get()
            investigator_user.is_staff = True
            investigator_user.save()

            self.client.force_login(self.staffuser)
            response = self.client.get(
                "/admin/registrar/domainrequest/",
                {
                    "investigator__id__exact": investigator_user.id,
                },
                follow=True,
            )

            # Then, test if the filter actually exists
            self.assertIn("filters", response.context)

            # Assert the content of filters and search_query
            filters = response.context["filters"]

            self.assertEqual(
                filters,
                [
                    {
                        "parameter_name": "investigator",
                        "parameter_value": "SomeGuy first_name:investigator SomeGuy last_name:investigator",
                    },
                ],
            )

    def test_investigator_dropdown_displays_only_staff(self):
        """
        This test verifies that the dropdown for the 'investigator' field in the DomainRequestAdmin
        interface only displays users who are marked as staff.

        It creates two DomainRequest instances, one with an investigator
        who is a staff user and another with an investigator who is not a staff user.

        It then retrieves the queryset for the 'investigator' dropdown from DomainRequestAdmin
        and checks that it matches the expected queryset, which only includes staff users.
        """

        with less_console_noise():
            # Create a mock DomainRequest object, with a fake investigator
            domain_request: DomainRequest = generic_domain_object("domain_request", "SomeGuy")
            investigator_user = User.objects.filter(username=domain_request.investigator.username).get()
            investigator_user.is_staff = True
            investigator_user.save()

            # Create a mock DomainRequest object, with a user that is not staff
            domain_request_2: DomainRequest = generic_domain_object("domain_request", "SomeOtherGuy")
            investigator_user_2 = User.objects.filter(username=domain_request_2.investigator.username).get()
            investigator_user_2.is_staff = False
            investigator_user_2.save()

            self.client.force_login(self.staffuser)

            request = self.factory.post("/admin/registrar/domainrequest/{}/change/".format(domain_request.pk))

            # Get the actual field from the model's meta information
            investigator_field = DomainRequest._meta.get_field("investigator")

            # We should only be displaying staff users, in alphabetical order
            sorted_fields = ["first_name", "last_name", "email"]
            expected_dropdown = list(User.objects.filter(is_staff=True).order_by(*sorted_fields))

            # Grab the current dropdown. We do an API call to autocomplete to get this info.
            domain_request_queryset = self.admin.formfield_for_foreignkey(investigator_field, request).queryset
            user_request = self.factory.post(
                "/admin/autocomplete/?app_label=registrar&model_name=domainrequest&field_name=investigator"
            )
            user_admin = MyUserAdmin(User, self.site)
            user_queryset = user_admin.get_search_results(user_request, domain_request_queryset, None)[0]
            current_dropdown = list(user_queryset)

            self.assertEqual(expected_dropdown, current_dropdown)

            # Non staff users should not be in the list
            self.assertNotIn(domain_request_2, current_dropdown)

    def test_investigator_list_is_alphabetically_sorted(self):
        """
        This test verifies that filter list for the 'investigator'
        is displayed alphabetically
        """
        with less_console_noise():
            # Create a mock DomainRequest object, with a fake investigator
            domain_request: DomainRequest = generic_domain_object("domain_request", "SomeGuy")
            investigator_user = User.objects.filter(username=domain_request.investigator.username).get()
            investigator_user.is_staff = True
            investigator_user.save()

            domain_request_2: DomainRequest = generic_domain_object("domain_request", "AGuy")
            investigator_user_2 = User.objects.filter(username=domain_request_2.investigator.username).get()
            investigator_user_2.first_name = "AGuy"
            investigator_user_2.is_staff = True
            investigator_user_2.save()

            domain_request_3: DomainRequest = generic_domain_object("domain_request", "FinalGuy")
            investigator_user_3 = User.objects.filter(username=domain_request_3.investigator.username).get()
            investigator_user_3.first_name = "FinalGuy"
            investigator_user_3.is_staff = True
            investigator_user_3.save()

            self.client.force_login(self.staffuser)
            request = RequestFactory().get("/")

            # These names have metadata embedded in them. :investigator implicitly tests if
            # these are actually from the attribute "investigator".
            expected_list = [
                "AGuy AGuy last_name:investigator",
                "FinalGuy FinalGuy last_name:investigator",
                "SomeGuy first_name:investigator SomeGuy last_name:investigator",
            ]

            # Get the actual sorted list of investigators from the lookups method
            actual_list = [item for _, item in self.admin.InvestigatorFilter.lookups(self, request, self.admin)]

            self.assertEqual(expected_list, actual_list)

    @less_console_noise_decorator
    def test_staff_can_see_cisa_region_federal(self):
        """Tests if staff can see CISA Region: N/A"""

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(_domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _domain_request.requested_domain.name)

        # Test if the page has the right CISA region
        expected_html = '<div class="flex-container margin-top-2"><span>CISA region: N/A</span></div>'
        # Remove whitespace from expected_html
        expected_html = "".join(expected_html.split())

        # Remove whitespace from response content
        response_content = "".join(response.content.decode().split())

        # Check if response contains expected_html
        self.assertIn(expected_html, response_content)

    @less_console_noise_decorator
    def test_staff_can_see_cisa_region_non_federal(self):
        """Tests if staff can see the correct CISA region"""

        # Create a fake domain request. State will be NY (2).
        _domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW, generic_org_type="interstate"
        )

        self.client.force_login(self.staffuser)
        response = self.client.get(
            "/admin/registrar/domainrequest/{}/change/".format(_domain_request.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _domain_request.requested_domain.name)

        # Test if the page has the right CISA region
        expected_html = '<div class="flex-container margin-top-2"><span>CISA region: 2</span></div>'
        # Remove whitespace from expected_html
        expected_html = "".join(expected_html.split())

        # Remove whitespace from response content
        response_content = "".join(response.content.decode().split())

        # Check if response contains expected_html
        self.assertIn(expected_html, response_content)


class TestDomainRequestAdminForm(TestCase):

    def test_form_choices(self):
        with less_console_noise():
            # Create a test domain request with an initial state of started
            domain_request = completed_domain_request()

            # Create a form instance with the test domain request
            form = DomainRequestAdminForm(instance=domain_request)

            # Verify that the form choices match the available transitions for started
            expected_choices = [("started", "Started"), ("submitted", "Submitted")]
            self.assertEqual(form.fields["status"].widget.choices, expected_choices)

            # cleanup
            domain_request.delete()

    def test_form_no_rejection_reason(self):
        with less_console_noise():
            # Create a test domain request with an initial state of started
            domain_request = completed_domain_request()

            # Create a form instance with the test domain request
            form = DomainRequestAdminForm(instance=domain_request)

            form = DomainRequestAdminForm(
                instance=domain_request,
                data={
                    "status": DomainRequest.DomainRequestStatus.REJECTED,
                    "rejection_reason": None,
                },
            )
            self.assertFalse(form.is_valid())
            self.assertIn("rejection_reason", form.errors)

            rejection_reason = form.errors.get("rejection_reason")
            self.assertEqual(rejection_reason, ["A reason is required for this status."])

            # cleanup
            domain_request.delete()

    def test_form_choices_when_no_instance(self):
        with less_console_noise():
            # Create a form instance without an instance
            form = DomainRequestAdminForm()

            # Verify that the form choices show all choices when no instance is provided;
            # this is necessary to show all choices when creating a new domain
            # request in django admin;
            # note that FSM ensures that no domain request exists with invalid status,
            # so don't need to test for invalid status
            self.assertEqual(
                form.fields["status"].widget.choices,
                DomainRequest._meta.get_field("status").choices,
            )

    def test_form_choices_when_ineligible(self):
        with less_console_noise():
            # Create a form instance with a domain request with ineligible status
            ineligible_domain_request = DomainRequest(status="ineligible")

            # Attempt to create a form with the ineligible domain request
            # The form should not raise an error, but choices should be the
            # full list of possible choices
            form = DomainRequestAdminForm(instance=ineligible_domain_request)

            self.assertEqual(
                form.fields["status"].widget.choices,
                DomainRequest._meta.get_field("status").choices,
            )
