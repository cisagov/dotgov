import datetime

from io import StringIO

from django.test import TestCase

from registrar.models import (
    User,
    Domain,
    DomainInvitation,
    TransitionDomain,
    DomainInformation,
    UserDomainRole,
)

from django.core.management import call_command
from unittest.mock import patch

from .common import less_console_noise

class TestOrganizationMigration(TestCase):
    def setUp(self):
        """ """
        # self.load_transition_domain_script = "load_transition_domain",
        # self.transfer_script = "transfer_transition_domains_to_domains",
        # self.master_script = "load_transition_domain",

        self.test_data_file_location = "registrar/tests/data"
        self.test_domain_contact_filename = "test_domain_contacts.txt"
        self.test_contact_filename = "test_contacts.txt"
        self.test_domain_status_filename = "test_domain_statuses.txt"

        # Files for parsing additional TransitionDomain data
        self.test_agency_adhoc_filename = "test_agency_adhoc.txt"
        self.test_authority_adhoc_filename = "test_authority_adhoc.txt"
        self.test_domain_additional = "test_domain_additional.txt"
        self.test_domain_types_adhoc = "test_domain_types_adhoc.txt"
        self.test_escrow_domains_daily = "test_escrow_domains_daily"
        self.test_organization_adhoc = "test_organization_adhoc.txt"
        self.migration_json_filename = "test_migrationFilepaths.json"

    def tearDown(self):
        # Delete domain information
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainInvitation.objects.all().delete()
        TransitionDomain.objects.all().delete()

        # Delete users
        User.objects.all().delete()
        UserDomainRole.objects.all().delete()

    def run_load_domains(self):
        # noqa here because splitting this up makes it confusing.
        # ES501
        with patch(
            "registrar.management.commands.utility.terminal_helper.TerminalHelper.query_yes_no_exit",  # noqa
            return_value=True,
        ):
            call_command(
                "load_transition_domain",
                self.migration_json_filename,
                directory=self.test_data_file_location,
            )

    def run_transfer_domains(self):
        call_command("transfer_transition_domains_to_domains")
    
    def run_load_organization_data(self):
        # noqa here (E501) because splitting this up makes it
        # confusing to read.
        with patch(
            "registrar.management.commands.utility.terminal_helper.TerminalHelper.query_yes_no_exit",  # noqa
            return_value=True,
        ):
            call_command(
                "load_organization_data",
                self.migration_json_filename,
                directory=self.test_data_file_location,
            )

    def compare_tables(
        self,
        expected_total_transition_domains,
        expected_total_domains,
        expected_total_domain_informations,
        expected_total_domain_invitations,
        expected_missing_domains,
        expected_duplicate_domains,
        expected_missing_domain_informations,
        expected_missing_domain_invitations,
    ):
        """Does a diff between the transition_domain and the following tables:
        domain, domain_information and the domain_invitation.
        Verifies that the data loaded correctly."""

        missing_domains = []
        duplicate_domains = []
        missing_domain_informations = []
        missing_domain_invites = []
        for transition_domain in TransitionDomain.objects.all():  # DEBUG:
            transition_domain_name = transition_domain.domain_name
            transition_domain_email = transition_domain.username

            # Check Domain table
            matching_domains = Domain.objects.filter(name=transition_domain_name)
            # Check Domain Information table
            matching_domain_informations = DomainInformation.objects.filter(domain__name=transition_domain_name)
            # Check Domain Invitation table
            matching_domain_invitations = DomainInvitation.objects.filter(
                email=transition_domain_email.lower(),
                domain__name=transition_domain_name,
            )

            if len(matching_domains) == 0:
                missing_domains.append(transition_domain_name)
            elif len(matching_domains) > 1:
                duplicate_domains.append(transition_domain_name)
            if len(matching_domain_informations) == 0:
                missing_domain_informations.append(transition_domain_name)
            if len(matching_domain_invitations) == 0:
                missing_domain_invites.append(transition_domain_name)

        total_missing_domains = len(missing_domains)
        total_duplicate_domains = len(duplicate_domains)
        total_missing_domain_informations = len(missing_domain_informations)
        total_missing_domain_invitations = len(missing_domain_invites)

        total_transition_domains = len(TransitionDomain.objects.all())
        total_domains = len(Domain.objects.all())
        total_domain_informations = len(DomainInformation.objects.all())
        total_domain_invitations = len(DomainInvitation.objects.all())

        print(
            f"""
        total_missing_domains = {len(missing_domains)}
        total_duplicate_domains = {len(duplicate_domains)}
        total_missing_domain_informations = {len(missing_domain_informations)}
        total_missing_domain_invitations = {total_missing_domain_invitations}

        total_transition_domains = {len(TransitionDomain.objects.all())}
        total_domains = {len(Domain.objects.all())}
        total_domain_informations = {len(DomainInformation.objects.all())}
        total_domain_invitations = {len(DomainInvitation.objects.all())}
        """
        )
        self.assertEqual(total_missing_domains, expected_missing_domains)
        self.assertEqual(total_duplicate_domains, expected_duplicate_domains)
        self.assertEqual(total_missing_domain_informations, expected_missing_domain_informations)
        self.assertEqual(total_missing_domain_invitations, expected_missing_domain_invitations)

        self.assertEqual(total_transition_domains, expected_total_transition_domains)
        self.assertEqual(total_domains, expected_total_domains)
        self.assertEqual(total_domain_informations, expected_total_domain_informations)
        self.assertEqual(total_domain_invitations, expected_total_domain_invitations)

    def test_load_organization_data_transition_domain(self):
        # == First, parse all existing data == #
        self.run_load_domains()
        self.run_transfer_domains()

        # == Second, try adding org data to it == #
        self.run_load_organization_data()

        # == Third, test that we've loaded data as we expect == #        
        transition_domains = TransitionDomain.objects.filter(domain_name="fakewebsite2.gov")

        # Should return three objects (three unique emails)
        self.assertEqual(transition_domains.count(), 3)

        # Lets test the first one
        transition = transition_domains.first()
        expected_transition_domain = TransitionDomain(
            id=24,
            username='alexandra.bobbitt5@test.com',
            domain_name='fakewebsite2.gov',
            status='on hold',
            email_sent=True,
            organization_type='Federal',
            organization_name='Fanoodle',
            federal_type='Executive',
            federal_agency='Department of Commerce',
            epp_creation_date=datetime.date(2004, 5, 7),
            epp_expiration_date=datetime.date(2023, 9, 30),
            first_name='Seline',
            middle_name='testmiddle2',
            last_name='Tower',
            title=None,
            email='stower3@answers.com',
            phone='151-539-6028',
            address_line='93001 Arizona Drive',
            city='Columbus',
            state_territory='Oh',
            zipcode='43268'
        )

        self.assertEqual(transition, expected_transition_domain)
    
    def test_load_organization_data_domain_information(self):
        # == First, parse all existing data == #
        self.run_load_domains()
        self.run_transfer_domains()

        # == Second, try adding org data to it == #
        self.run_load_organization_data()

        # == Third, test that we've loaded data as we expect == #     
        _domain = Domain.objects.filter(name="fakewebsite2.gov").get()   
        domain_information = DomainInformation.objects.filter(domain=_domain).get()
        expected_domain_information = DomainInformation(
            id=4,
            creator_id=1,
            domain_application_id=None,
            organization_type='federal',
            federally_recognized_tribe=None,
            state_recognized_tribe=None,
            tribe_name=None,
            federal_agency='Department of Commerce',
            federal_type='executive',
            is_election_board=None,
            organization_name='Fanoodle',
            address_line1='93001 Arizona Drive',
            address_line2=None,
            city='Columbus',
            state_territory='Oh',
            zipcode='43268',
            urbanization=None,
            about_your_organization=None,
            authorizing_official_id=5,
            domain_id=4,
            submitter_id=None,
            purpose=None,
            no_other_contacts_rationale=None,
            anything_else=None,
            is_policy_acknowledged=None
        )
        self.assertEqual(domain_information, expected_domain_information)

    def test_load_organization_data_integrity(self):
        """Validates data integrity with the load_org_data command"""
        # First, parse all existing data
        self.run_load_domains()
        self.run_transfer_domains()

        # Second, try adding org data to it
        self.run_load_organization_data()
        
        # Third, test that we didn't corrupt any data
        expected_total_transition_domains = 9
        expected_total_domains = 5
        expected_total_domain_informations = 5
        expected_total_domain_invitations = 8

        expected_missing_domains = 0
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 0
        # we expect 1 missing invite from anomaly.gov (an injected error)
        expected_missing_domain_invitations = 1
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

class TestMigrations(TestCase):
    def setUp(self):
        """ """
        # self.load_transition_domain_script = "load_transition_domain",
        # self.transfer_script = "transfer_transition_domains_to_domains",
        # self.master_script = "load_transition_domain",

        self.test_data_file_location = "registrar/tests/data"
        self.test_domain_contact_filename = "test_domain_contacts.txt"
        self.test_contact_filename = "test_contacts.txt"
        self.test_domain_status_filename = "test_domain_statuses.txt"

        # Files for parsing additional TransitionDomain data
        self.test_agency_adhoc_filename = "test_agency_adhoc.txt"
        self.test_authority_adhoc_filename = "test_authority_adhoc.txt"
        self.test_domain_additional = "test_domain_additional.txt"
        self.test_domain_types_adhoc = "test_domain_types_adhoc.txt"
        self.test_escrow_domains_daily = "test_escrow_domains_daily"
        self.test_organization_adhoc = "test_organization_adhoc.txt"
        self.migration_json_filename = "test_migrationFilepaths.json"

    def tearDown(self):
        super().tearDown()
        # Delete domain information
        TransitionDomain.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainInvitation.objects.all().delete()

        # Delete users
        User.objects.all().delete()
        UserDomainRole.objects.all().delete()

    def run_load_domains(self):
        # noqa here because splitting this up makes it confusing.
        # ES501
        with patch(
            "registrar.management.commands.utility.terminal_helper.TerminalHelper.query_yes_no_exit",  # noqa
            return_value=True,
        ):
            call_command(
                "load_transition_domain",
                self.migration_json_filename,
                directory=self.test_data_file_location,
            )

    def run_transfer_domains(self):
        call_command("transfer_transition_domains_to_domains")

    def run_master_script(self):
        # noqa here (E501) because splitting this up makes it
        # confusing to read.
        with patch(
            "registrar.management.commands.utility.terminal_helper.TerminalHelper.query_yes_no_exit",  # noqa
            return_value=True,
        ):
            call_command(
                "master_domain_migrations",
                runMigrations=True,
                migrationDirectory=self.test_data_file_location,
                migrationJSON=self.migration_json_filename,
                disablePrompts=True,
            )
    
    def run_load_organization_data(self):
        # noqa here (E501) because splitting this up makes it
        # confusing to read.
        with patch(
            "registrar.management.commands.utility.terminal_helper.TerminalHelper.query_yes_no_exit",  # noqa
            return_value=True,
        ):
            call_command(
                "load_organization_data",
                self.migration_json_filename,
                directory=self.test_data_file_location,
            )

    def compare_tables(
        self,
        expected_total_transition_domains,
        expected_total_domains,
        expected_total_domain_informations,
        expected_total_domain_invitations,
        expected_missing_domains,
        expected_duplicate_domains,
        expected_missing_domain_informations,
        expected_missing_domain_invitations,
    ):
        """Does a diff between the transition_domain and the following tables:
        domain, domain_information and the domain_invitation.
        Verifies that the data loaded correctly."""

        missing_domains = []
        duplicate_domains = []
        missing_domain_informations = []
        missing_domain_invites = []
        for transition_domain in TransitionDomain.objects.all():  # DEBUG:
            transition_domain_name = transition_domain.domain_name
            transition_domain_email = transition_domain.username

            # Check Domain table
            matching_domains = Domain.objects.filter(name=transition_domain_name)
            # Check Domain Information table
            matching_domain_informations = DomainInformation.objects.filter(domain__name=transition_domain_name)
            # Check Domain Invitation table
            matching_domain_invitations = DomainInvitation.objects.filter(
                email=transition_domain_email.lower(),
                domain__name=transition_domain_name,
            )

            if len(matching_domains) == 0:
                missing_domains.append(transition_domain_name)
            elif len(matching_domains) > 1:
                duplicate_domains.append(transition_domain_name)
            if len(matching_domain_informations) == 0:
                missing_domain_informations.append(transition_domain_name)
            if len(matching_domain_invitations) == 0:
                missing_domain_invites.append(transition_domain_name)

        total_missing_domains = len(missing_domains)
        total_duplicate_domains = len(duplicate_domains)
        total_missing_domain_informations = len(missing_domain_informations)
        total_missing_domain_invitations = len(missing_domain_invites)

        total_transition_domains = len(TransitionDomain.objects.all())
        total_domains = len(Domain.objects.all())
        total_domain_informations = len(DomainInformation.objects.all())
        total_domain_invitations = len(DomainInvitation.objects.all())

        print(
            f"""
        total_missing_domains = {len(missing_domains)}
        total_duplicate_domains = {len(duplicate_domains)}
        total_missing_domain_informations = {len(missing_domain_informations)}
        total_missing_domain_invitations = {total_missing_domain_invitations}

        total_transition_domains = {len(TransitionDomain.objects.all())}
        total_domains = {len(Domain.objects.all())}
        total_domain_informations = {len(DomainInformation.objects.all())}
        total_domain_invitations = {len(DomainInvitation.objects.all())}
        """
        )
        self.assertEqual(total_missing_domains, expected_missing_domains)
        self.assertEqual(total_duplicate_domains, expected_duplicate_domains)
        self.assertEqual(total_missing_domain_informations, expected_missing_domain_informations)
        self.assertEqual(total_missing_domain_invitations, expected_missing_domain_invitations)

        self.assertEqual(total_transition_domains, expected_total_transition_domains)
        self.assertEqual(total_domains, expected_total_domains)
        self.assertEqual(total_domain_informations, expected_total_domain_informations)
        self.assertEqual(total_domain_invitations, expected_total_domain_invitations)

    def test_master_migration_functions(self):
        """Run the full master migration script using local test data.
        NOTE: This is more of an integration test and so far does not
        follow best practice of limiting the number of assertions per test.
        But for now, this will double-check that the script
        works as intended."""

        self.run_master_script()

        # STEP 2: (analyze the tables just like the
        # migration script does, but add assert statements)
        expected_total_transition_domains = 9
        expected_total_domains = 5
        expected_total_domain_informations = 5
        expected_total_domain_invitations = 8

        expected_missing_domains = 0
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 0
        # we expect 1 missing invite from anomaly.gov (an injected error)
        expected_missing_domain_invitations = 1
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

    def test_load_empty_transition_domain(self):
        """Loads TransitionDomains without additional data"""
        self.run_load_domains()

        # STEP 2: (analyze the tables just like the migration
        # script does, but add assert statements)
        expected_total_transition_domains = 9
        expected_total_domains = 0
        expected_total_domain_informations = 0
        expected_total_domain_invitations = 0

        expected_missing_domains = 9
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 9
        expected_missing_domain_invitations = 9
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

    def test_load_full_domain(self):
        self.run_load_domains()
        self.run_transfer_domains()

        # Analyze the tables
        expected_total_transition_domains = 9
        expected_total_domains = 5
        expected_total_domain_informations = 5
        expected_total_domain_invitations = 8

        expected_missing_domains = 0
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 0
        expected_missing_domain_invitations = 1
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

        # Test created domains
        anomaly_domains = Domain.objects.filter(name="anomaly.gov")
        self.assertEqual(anomaly_domains.count(), 1)
        anomaly = anomaly_domains.get()

        self.assertEqual(anomaly.expiration_date, datetime.date(2023, 3, 9))

        self.assertEqual(anomaly.name, "anomaly.gov")
        self.assertEqual(anomaly.state, "ready")

        testdomain_domains = Domain.objects.filter(name="fakewebsite2.gov")
        self.assertEqual(testdomain_domains.count(), 1)

        testdomain = testdomain_domains.get()

        self.assertEqual(testdomain.expiration_date, datetime.date(2023, 9, 30))
        self.assertEqual(testdomain.name, "fakewebsite2.gov")
        self.assertEqual(testdomain.state, "on hold")

    def test_load_full_domain_information(self):
        self.run_load_domains()
        self.run_transfer_domains()

        # Analyze the tables
        expected_total_transition_domains = 9
        expected_total_domains = 5
        expected_total_domain_informations = 5
        expected_total_domain_invitations = 8

        expected_missing_domains = 0
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 0
        expected_missing_domain_invitations = 1
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

        # Test created Domain Information objects
        domain = Domain.objects.filter(name="anomaly.gov").get()
        anomaly_domain_infos = DomainInformation.objects.filter(domain=domain)

        self.assertEqual(anomaly_domain_infos.count(), 1)

        # This domain should be pretty barebones. Something isnt
        # parsing right if we get a lot of data.
        anomaly = anomaly_domain_infos.get()
        self.assertEqual(anomaly.organization_name, "Flashdog")
        self.assertEqual(anomaly.organization_type, None)
        self.assertEqual(anomaly.federal_agency, None)
        self.assertEqual(anomaly.federal_type, None)

        # Check for the "system" creator user
        Users = User.objects.filter(username="System")
        self.assertEqual(Users.count(), 1)
        self.assertEqual(anomaly.creator, Users.get())

        domain = Domain.objects.filter(name="fakewebsite2.gov").get()
        fakewebsite_domain_infos = DomainInformation.objects.filter(domain=domain)
        self.assertEqual(fakewebsite_domain_infos.count(), 1)

        fakewebsite = fakewebsite_domain_infos.get()
        self.assertEqual(fakewebsite.organization_name, "Fanoodle")
        self.assertEqual(fakewebsite.organization_type, "federal")
        self.assertEqual(fakewebsite.federal_agency, "Department of Commerce")
        self.assertEqual(fakewebsite.federal_type, "executive")

        ao = fakewebsite.authorizing_official

        self.assertEqual(ao.first_name, "Seline")
        self.assertEqual(ao.middle_name, "testmiddle2")
        self.assertEqual(ao.last_name, "Tower")
        self.assertEqual(ao.email, "stower3@answers.com")
        self.assertEqual(ao.phone, "151-539-6028")

        # Check for the "system" creator user
        Users = User.objects.filter(username="System")
        self.assertEqual(Users.count(), 1)
        self.assertEqual(anomaly.creator, Users.get())

    def test_transfer_transition_domains_to_domains(self):
        self.run_load_domains()
        self.run_transfer_domains()

        # Analyze the tables
        expected_total_transition_domains = 9
        expected_total_domains = 5
        expected_total_domain_informations = 5
        expected_total_domain_invitations = 8

        expected_missing_domains = 0
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 0
        expected_missing_domain_invitations = 1
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

    def test_logins(self):
        # TODO: setup manually instead of calling other scripts
        self.run_load_domains()
        self.run_transfer_domains()

        # Simluate Logins
        for invite in DomainInvitation.objects.all():
            # get a user with this email address
            user, user_created = User.objects.get_or_create(email=invite.email, username=invite.email)
            user.on_each_login()

        # Analyze the tables
        expected_total_transition_domains = 9
        expected_total_domains = 5
        expected_total_domain_informations = 5
        expected_total_domain_invitations = 8

        expected_missing_domains = 0
        expected_duplicate_domains = 0
        expected_missing_domain_informations = 0
        expected_missing_domain_invitations = 1
        self.compare_tables(
            expected_total_transition_domains,
            expected_total_domains,
            expected_total_domain_informations,
            expected_total_domain_invitations,
            expected_missing_domains,
            expected_duplicate_domains,
            expected_missing_domain_informations,
            expected_missing_domain_invitations,
        )

    def test_send_domain_invitations_email(self):
        """Can send only a single domain invitation email."""
        with less_console_noise():
            self.run_load_domains()
            self.run_transfer_domains()

        # this is one of the email addresses in data/test_contacts.txt
        output_stream = StringIO()
        # also have to re-point the logging handlers to output_stream
        with less_console_noise(output_stream):
            call_command("send_domain_invitations", "testuser@gmail.com", stdout=output_stream)

        # Check that we had the right numbers in our output
        output = output_stream.getvalue()
        # should only be one domain we send email for
        self.assertIn("Found 1 transition domains", output)
        self.assertTrue("would send email to testuser@gmail.com", output)

    def test_send_domain_invitations_two_emails(self):
        """Can send only a single domain invitation email."""
        with less_console_noise():
            self.run_load_domains()
            self.run_transfer_domains()

        # these are two email addresses in data/test_contacts.txt
        output_stream = StringIO()
        # also have to re-point the logging handlers to output_stream
        with less_console_noise(output_stream):
            call_command(
                "send_domain_invitations", "testuser@gmail.com", "agustina.wyman7@test.com", stdout=output_stream
            )

        # Check that we had the right numbers in our output
        output = output_stream.getvalue()
        # should only be one domain we send email for
        self.assertIn("Found 2 transition domains", output)
        self.assertTrue("would send email to testuser@gmail.com", output)
        self.assertTrue("would send email to agustina.wyman7@test.com", output)
