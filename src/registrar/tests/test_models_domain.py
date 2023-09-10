"""
Feature being tested: Registry Integration

This file tests the various ways in which the registrar interacts with the registry.
"""
from django.test import TestCase
from django.db.utils import IntegrityError
from unittest.mock import patch, MagicMock, call
import datetime
from registrar.models import Domain  # add in DomainApplication, User,

from unittest import skip
from epplibwrapper import commands, common, RegistryError, ErrorCode
from registrar.models.domain_application import DomainApplication
from registrar.models.domain_information import DomainInformation
from registrar.models.draft_domain import DraftDomain
from registrar.models.public_contact import PublicContact
from registrar.models.user import User


class MockEppLib(TestCase):
    class fakedEppObject(object):
        """"""

        def __init__(self, auth_info=..., cr_date=..., contacts=..., hosts=...):
            self.auth_info = auth_info
            self.cr_date = cr_date
            self.contacts = contacts
            self.hosts = hosts

    mockDataInfoDomain = fakedEppObject(
        "fakepw",
        cr_date=datetime.datetime(2023, 5, 25, 19, 45, 35),
        contacts=["123"],
        hosts=["fake.host.com"],
    )
    infoDomainNoContact = fakedEppObject(
        "security",
        cr_date=datetime.datetime(2023, 5, 25, 19, 45, 35),
        contacts=[],
        hosts=["fake.host.com"],
    )
    mockDataInfoContact = fakedEppObject(
        "anotherPw", cr_date=datetime.datetime(2023, 7, 25, 19, 45, 35)
    )
    mockDataInfoHosts = fakedEppObject(
        "lastPw", cr_date=datetime.datetime(2023, 8, 25, 19, 45, 35)
    )

    def mockSend(self, _request, cleaned):
        """"""
        print("in mock send patch is ")
        print(_request)
        if isinstance(_request, commands.InfoDomain):
            if getattr(_request, "name", None) == "security.gov":
                return MagicMock(res_data=[self.infoDomainNoContact])
            return MagicMock(res_data=[self.mockDataInfoDomain])
        elif isinstance(_request, commands.InfoContact):
            return MagicMock(res_data=[self.mockDataInfoContact])
        elif (
            isinstance(_request, commands.CreateContact)
            and getattr(_request, "id", None) == "fail"
            and self.mockedSendFunction.call_count == 3
        ):
            print("raising error")
            print()
            raise RegistryError(code=ErrorCode.OBJECT_EXISTS)
        return MagicMock(res_data=[self.mockDataInfoHosts])

    def setUp(self):
        """mock epp send function as this will fail locally"""
        self.mockSendPatch = patch("registrar.models.domain.registry.send")
        self.mockedSendFunction = self.mockSendPatch.start()
        self.mockedSendFunction.side_effect = self.mockSend
        
    def _convertPublicContactToEpp(self, contact: PublicContact, disclose_email=False, createContact=True):
        DF = common.DiscloseField
        fields = {DF.FAX, DF.VOICE, DF.ADDR}

        if not disclose_email:
            fields.add(DF.EMAIL)

        di = common.Disclose(
            flag=False,
            fields=fields,
            types={DF.ADDR: "loc"},
        )
        # check docs here looks like we may have more than one address field but
        addr = common.ContactAddr(
            street=[
                contact.street1,
                contact.street2,
                contact.street3,
            ],
            city=contact.city,
            pc=contact.pc,
            cc=contact.cc,
            sp=contact.sp,
        )

        pi = common.PostalInfo(
            name=contact.name,
            addr=addr,
            org=contact.org,
            type="loc",
        )
        ai = common.ContactAuthInfo(pw="2fooBAR123fooBaz")
        if createContact:
            return commands.CreateContact(
                id=contact.registry_id,
                postal_info=pi,
                email=contact.email,
                voice=contact.voice,
                fax=contact.fax,
                auth_info=ai,
                disclose=di,
                vat=None,
                ident=None,
                notify_email=None,
            )
        else:
            return commands.UpdateContact(
            id=contact.registry_id,
            postal_info=pi,
            email=contact.email,
            voice=contact.voice,
            fax=contact.fax,
        )

    def tearDown(self):
        self.mockSendPatch.stop()


class TestDomainCache(MockEppLib):
    # def setUp(self):
    #     #call setup from the mock epplib
    #     super().setUp()

    # def tearDown(self):
    #     #call setup from the mock epplib
    #     super().tearDown()

    def test_cache_sets_resets(self):
        """Cache should be set on getter and reset on setter calls"""
        domain, _ = Domain.objects.get_or_create(name="igorville.gov")
        # trigger getter
        _ = domain.creation_date

        # getter should set the domain cache with a InfoDomain object
        # (see InfoDomainResult)
        self.assertEquals(domain._cache["auth_info"], self.mockDataInfoDomain.auth_info)
        self.assertEquals(domain._cache["cr_date"], self.mockDataInfoDomain.cr_date)
        self.assertFalse("avail" in domain._cache.keys())

        # using a setter should clear the cache
        domain.nameservers = [("", "")]
        self.assertEquals(domain._cache, {})

        # send should have been called only once
        self.mockedSendFunction.assert_called_once()

    def test_cache_used_when_avail(self):
        """Cache is pulled from if the object has already been accessed"""
        domain, _ = Domain.objects.get_or_create(name="igorville.gov")
        cr_date = domain.creation_date

        # repeat the getter call
        cr_date = domain.creation_date

        # value should still be set correctly
        self.assertEqual(cr_date, self.mockDataInfoDomain.cr_date)
        self.assertEqual(domain._cache["cr_date"], self.mockDataInfoDomain.cr_date)

        # send was only called once & not on the second getter call
        self.mockedSendFunction.assert_called_once()

    def test_cache_nested_elements(self):
        """Cache works correctly with the nested objects cache and hosts"""
        domain, _ = Domain.objects.get_or_create(name="igorville.gov")

        # the cached contacts and hosts should be dictionaries of what is passed to them
        expectedContactsDict = {
            "id": self.mockDataInfoDomain.contacts[0],
            "auth_info": self.mockDataInfoContact.auth_info,
            "cr_date": self.mockDataInfoContact.cr_date,
        }
        expectedHostsDict = {
            "name": self.mockDataInfoDomain.hosts[0],
            "cr_date": self.mockDataInfoHosts.cr_date,
        }

        # this can be changed when the getter for contacts is implemented
        domain._get_property("contacts")

        # check domain info is still correct and not overridden
        self.assertEqual(domain._cache["auth_info"], self.mockDataInfoDomain.auth_info)
        self.assertEqual(domain._cache["cr_date"], self.mockDataInfoDomain.cr_date)

        # check contacts
        self.assertEqual(domain._cache["_contacts"], self.mockDataInfoDomain.contacts)
        self.assertEqual(domain._cache["contacts"], [expectedContactsDict])

        # get and check hosts is set correctly
        domain._get_property("hosts")
        self.assertEqual(domain._cache["hosts"], [expectedHostsDict])
        ##IS THERE AN ERROR HERE???,


class TestDomainCreation(TestCase):
    """Rule: An approved domain application must result in a domain"""

    # def setUp(self):
    #     """
    #     Background:
    #         Given that a valid domain application exists
    #     """

    def test_approved_application_creates_domain_locally(self):
        """
        Scenario: Analyst approves a domain application
            When the DomainApplication transitions to approved
            Then a Domain exists in the database with the same `name`
            But a domain object does not exist in the registry
        """
        patcher = patch("registrar.models.domain.Domain._get_or_create_domain")
        mocked_domain_creation = patcher.start()
        draft_domain, _ = DraftDomain.objects.get_or_create(name="igorville.gov")
        user, _ = User.objects.get_or_create()
        application = DomainApplication.objects.create(
            creator=user, requested_domain=draft_domain
        )
        # skip using the submit method
        application.status = DomainApplication.SUBMITTED
        # transition to approve state
        application.approve()
        # should hav information present for this domain
        domain = Domain.objects.get(name="igorville.gov")
        self.assertTrue(domain)
        mocked_domain_creation.assert_not_called()

    @skip("not implemented yet")
    def test_accessing_domain_properties_creates_domain_in_registry(self):
        """
        Scenario: A registrant checks the status of a newly approved domain
            Given that no domain object exists in the registry
            When a property is accessed
            Then Domain sends `commands.CreateDomain` to the registry
            And `domain.state` is set to `CREATED`
            And `domain.is_active()` returns False
        """
        raise

    def test_empty_domain_creation(self):
        """Can't create a completely empty domain."""
        with self.assertRaisesRegex(IntegrityError, "name"):
            Domain.objects.create()

    def test_minimal_creation(self):
        """Can create with just a name."""
        Domain.objects.create(name="igorville.gov")

    def test_duplicate_creation(self):
        """Can't create domain if name is not unique."""
        Domain.objects.create(name="igorville.gov")
        with self.assertRaisesRegex(IntegrityError, "name"):
            Domain.objects.create(name="igorville.gov")

    @skip("cannot activate a domain without mock registry")
    def test_get_status(self):
        """Returns proper status based on `state`."""
        domain = Domain.objects.create(name="igorville.gov")
        domain.save()
        self.assertEqual(None, domain.status)
        domain.activate()
        domain.save()
        self.assertIn("ok", domain.status)

    def tearDown(self) -> None:
        Domain.objects.delete()
        # User.objects.delete()


class TestRegistrantContacts(MockEppLib):
    """Rule: Registrants may modify their WHOIS data"""

    def setUp(self):
        """
        Background:
            Given the registrant is logged in
            And the registrant is the admin on a domain
        """
        super().setUp()
        self.domain, _ = Domain.objects.get_or_create(name="security.gov")

    def tearDown(self):
        super().tearDown()
        # self.contactMailingAddressPatch.stop()
        # self.createContactPatch.stop()

    def test_no_security_email(self):
        """
        Scenario: Registrant has not added a security contact email
            Given `domain.security_contact` has not been set to anything
            When the domain is created in the registry
            Then the domain has a valid security contact with CISA defaults
            And disclose flags are set to keep the email address hidden
        """

        # making a domain should make it domain
        expectedSecContact = PublicContact.get_default_security()
        expectedSecContact.domain = self.domain

        self.domain.pendingCreate()

        assert self.mockedSendFunction.call_count == 8
        assert PublicContact.objects.filter(domain=self.domain).count() == 4
        assert (
            PublicContact.objects.get(
                domain=self.domain,
                contact_type=PublicContact.ContactTypeChoices.SECURITY,
            ).email
            == expectedSecContact.email
        )

        id = PublicContact.objects.get(
            domain=self.domain,
            contact_type=PublicContact.ContactTypeChoices.SECURITY,
        ).registry_id

        expectedSecContact.registry_id = id
        expectedCreateCommand = self._convertPublicContactToEpp(
            expectedSecContact, disclose_email=False
        )
        expectedUpdateDomain = commands.UpdateDomain(
            name=self.domain.name,
            add=[
                common.DomainContact(
                    contact=expectedSecContact.registry_id, type="security"
                )
            ],
        )

        self.mockedSendFunction.assert_any_call(expectedCreateCommand, cleaned=True)
        self.mockedSendFunction.assert_any_call(expectedUpdateDomain, cleaned=True)

    def test_user_adds_security_email(self):
        """
        Scenario: Registrant adds a security contact email
            When `domain.security_contact` is set equal to a PublicContact with the
                chosen security contact email
            Then Domain sends `commands.CreateContact` to the registry
            And Domain sends `commands.UpdateDomain` to the registry with the newly
                created contact of type 'security'
        """
        # make a security contact that is a PublicContact
        self.domain.pendingCreate()  ##make sure a security email already exists
        expectedSecContact = PublicContact.get_default_security()
        expectedSecContact.domain = self.domain
        expectedSecContact.email = "newEmail@fake.com"
        expectedSecContact.registry_id = "456"
        expectedSecContact.name = "Fakey McFakerson"

        # calls the security contact setter as if you did
        #  self.domain.security_contact=expectedSecContact
        expectedSecContact.save()

        # check create contact sent with email
        # DF = common.DiscloseField
        # di = common.Disclose(
        #     flag=False, fields={DF.FAX, DF.VOICE, DF.ADDR, DF.EMAIL}, types={DF.ADDR: "loc"}
        # )

        # addr = common.ContactAddr(
        #     street=[
        #         expectedSecContact.street1,
        #         expectedSecContact.street2,
        #         expectedSecContact.street3,
        #     ],
        #     city=expectedSecContact.city,
        #     pc=expectedSecContact.pc,
        #     cc=expectedSecContact.cc,
        #     sp=expectedSecContact.sp,
        # )
        # pi = common.PostalInfo(
        #     name=expectedSecContact.name,
        #     addr=addr,
        #     org=expectedSecContact.org,
        #     type="loc",
        # )
        # ai = common.ContactAuthInfo(pw="2fooBAR123fooBaz")

        # no longer the default email it should be disclosed!!
        expectedCreateCommand = self._convertPublicContactToEpp(
            expectedSecContact, disclose_email=True
        )

        # commands.CreateContact(
        #     id=expectedSecContact.registry_id,
        #     postal_info=pi,
        #     email=expectedSecContact.email,
        #     voice=expectedSecContact.voice,
        #     fax=expectedSecContact.fax,
        #     auth_info=ai,
        #     disclose=di,
        #     vat=None,
        #     ident=None,
        #     notify_email=None,
        # )
        expectedUpdateDomain = commands.UpdateDomain(
            name=self.domain.name,
            add=[
                common.DomainContact(
                    contact=expectedSecContact.registry_id, type="security"
                )
            ],
        )

        # check that send has triggered the create command for the contact
        print("finishing")

        print(PublicContact.objects.filter(domain=self.domain))
        receivedSecurityContact = PublicContact.objects.get(
            domain=self.domain, contact_type=PublicContact.ContactTypeChoices.SECURITY
        )

        print(self.mockedSendFunction.call_count)
        print(self.mockedSendFunction.call_args_list)
        # assert( self.mockedSendFunction.call_count == 3)
        assert receivedSecurityContact == expectedSecContact
        self.mockedSendFunction.assert_any_call(expectedCreateCommand, cleaned=True)
        self.mockedSendFunction.assert_any_call(expectedUpdateDomain, cleaned=True)

    def test_security_email_is_idempotent(self):
        """
        Scenario: Registrant adds a security contact email twice, due to a UI glitch
            When `commands.CreateContact` and `commands.UpdateDomain` are sent
                to the registry twice with identical data
            Then no errors are raised in Domain
        """
        # self.domain.pendingCreate() ##make sure a security email already exists
        security_contact = self.domain.get_default_security_contact()
        security_contact.registry_id = "fail"
        security_contact.save()

        self.domain.security_contact = security_contact

        print(self.mockedSendFunction.call_args_list)
        expectedCreateCommand = self._convertPublicContactToEpp(
            security_contact, disclose_email=False
        )
        print(expectedCreateCommand)
        expectedUpdateDomain = commands.UpdateDomain(
            name=self.domain.name,
            add=[
                common.DomainContact(
                    contact=security_contact.registry_id, type="security"
                )
            ],
        )
        expected_calls = [
            call(expectedCreateCommand, cleaned=True),
            call(expectedCreateCommand, cleaned=True),
            call(expectedUpdateDomain, cleaned=True),
        ]
        self.mockedSendFunction.assert_has_calls(expected_calls, any_order=True)
        assert PublicContact.objects.filter(domain=self.domain).count() == 1

    def test_user_deletes_security_email(self):
        """
        Scenario: Registrant clears out an existing security contact email
            Given a domain exists in the registry with a user-added security email
            When `domain.security_contact` is set equal to a PublicContact with an empty
                security contact email
            Then Domain sends `commands.UpdateDomain` and `commands.DeleteContact`
                to the registry
            And the domain has a valid security contact with CISA defaults
            And disclose flags are set to keep the email address hidden
        """
        old_contact = self.domain.get_default_security_contact()

        old_contact.registry_id = "fail"
        old_contact.email = "user.entered@email.com"
        old_contact.save()
        new_contact = self.domain.get_default_security_contact()
        new_contact.registry_id = "fail"
        new_contact.email = ""
        self.domain.security_contact=new_contact

        print("old contact %s  email is %s" % (str(old_contact), str(old_contact.email)))
        print("new contact %s " % new_contact)
        firstCreateContactCall = self._convertPublicContactToEpp(
            old_contact, disclose_email=True
        )
        updateDomainAddCall = commands.UpdateDomain(
            name=self.domain.name,
            add=[
                common.DomainContact(contact=old_contact.registry_id, type="security")
            ],
        )
        print( PublicContact.objects.filter(domain=self.domain))
        print("just printed the objects for public contact!!")
    
        assert (
            PublicContact.objects.filter(domain=self.domain).get().email
            == PublicContact.get_default_security().email
        )
        # this one triggers the fail
        secondCreateContact = self._convertPublicContactToEpp(
            new_contact, disclose_email=True
        )
        updateDomainRemCall = commands.UpdateDomain(
            name=self.domain.name,
            rem=[
                common.DomainContact(contact=old_contact.registry_id, type="security")
            ],
        )
        args = self.mockedSendFunction.call_args_list
        print("actualy args printing ******")
        print(args)
        print(len(args))
        defaultSecID = (
            PublicContact.objects.filter(domain=self.domain).get().registry_id
        )
        default_security = PublicContact.get_default_security()
        default_security.registry_id = defaultSecID
        createDefaultContact = self._convertPublicContactToEpp(
            default_security, disclose_email=False
        )
        updateDomainWDefault = commands.UpdateDomain(
            name=self.domain.name,
            add=[common.DomainContact(contact=defaultSecID, type="security")],
        )

        expected_calls = [
            call(firstCreateContactCall, cleaned=True),
            call(updateDomainAddCall, cleaned=True),
            call(secondCreateContact, cleaned=True),
            call(updateDomainRemCall, cleaned=True),
            call(createDefaultContact, cleaned=True),
            call(updateDomainWDefault, cleaned=True),
        ]

        args = self.mockedSendFunction.call_args_list
        print("actualy args printing ******")
        print(args)
        print(len(args))

        print(len(expected_calls))
        print("\n\n\n expected calls now printing\n")
        print(expected_calls)
        self.mockedSendFunction.assert_has_calls(expected_calls, any_order=True)


    def test_updates_security_email(self):
        """
        Scenario: Registrant replaces one valid security contact email with another
            Given a domain exists in the registry with a user-added security email
            When `domain.security_contact` is set equal to a PublicContact with a new
                security contact email
            Then Domain sends `commands.UpdateContact` to the registry
        """
        security_contact = self.domain.get_default_security_contact()
        security_contact.email="originalUserEmail@gmail.com"
        security_contact.registry_id = "fail"
        security_contact.save()
        expectedCreateCommand = self._convertPublicContactToEpp(
            security_contact, disclose_email=True
        )
        print(expectedCreateCommand)
        expectedUpdateDomain = commands.UpdateDomain(
            name=self.domain.name,
            add=[
                common.DomainContact(
                    contact=security_contact.registry_id, type="security"
                )
            ],
        )
        security_contact.email="changedEmail@email.com"
        expectedSecondCreateCommand = self._convertPublicContactToEpp(
            security_contact, disclose_email=True
        )
        updateContact=self._convertPublicContactToEpp(security_contact,disclose_email=True,createContact=False)
        print(expectedSecondCreateCommand)
       
        print(self.mockedSendFunction.call_args_list)

        expected_calls = [
            call(expectedCreateCommand, cleaned=True),
            call(expectedUpdateDomain, cleaned=True),
            call(expectedSecondCreateCommand,cleaned=True),
            call(updateContact, cleaned=True),
        ]
        self.mockedSendFunction.assert_has_calls(expected_calls, any_order=True)
        assert PublicContact.objects.filter(domain=self.domain).count() == 1
        

    @skip("not implemented yet")
    def test_update_is_unsuccessful(self):
        """
        Scenario: An update to the security contact is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
        """
        raise


class TestRegistrantNameservers(TestCase):
    """Rule: Registrants may modify their nameservers"""

    def setUp(self):
        """
        Background:
            Given the registrant is logged in
            And the registrant is the admin on a domain
        """
        pass

    @skip("not implemented yet")
    def test_user_adds_one_nameserver(self):
        """
        Scenario: Registrant adds a single nameserver
            Given the domain has zero nameservers
            When `domain.nameservers` is set to an array of length 1
            Then `commands.CreateHost` and `commands.UpdateDomain` is sent
                to the registry
            And `domain.is_active` returns False
        """
        raise

    @skip("not implemented yet")
    def test_user_adds_two_nameservers(self):
        """
        Scenario: Registrant adds 2 or more nameservers, thereby activating the domain
            Given the domain has zero nameservers
            When `domain.nameservers` is set to an array of length 2
            Then `commands.CreateHost` and `commands.UpdateDomain` is sent
                to the registry
            And `domain.is_active` returns True
        """
        raise

    @skip("not implemented yet")
    def test_user_adds_too_many_nameservers(self):
        """
        Scenario: Registrant adds 14 or more nameservers
            Given the domain has zero nameservers
            When `domain.nameservers` is set to an array of length 14
            Then Domain raises a user-friendly error
        """
        raise

    @skip("not implemented yet")
    def test_user_removes_some_nameservers(self):
        """
        Scenario: Registrant removes some nameservers, while keeping at least 2
            Given the domain has 3 nameservers
            When `domain.nameservers` is set to an array containing nameserver #1 and #2
            Then `commands.UpdateDomain` and `commands.DeleteHost` is sent
                to the registry
            And `domain.is_active` returns True
        """
        raise

    @skip("not implemented yet")
    def test_user_removes_too_many_nameservers(self):
        """
        Scenario: Registrant removes some nameservers, bringing the total to less than 2
            Given the domain has 3 nameservers
            When `domain.nameservers` is set to an array containing nameserver #1
            Then `commands.UpdateDomain` and `commands.DeleteHost` is sent
                to the registry
            And `domain.is_active` returns False
        """
        raise

    @skip("not implemented yet")
    def test_user_replaces_nameservers(self):
        """
        Scenario: Registrant simultaneously adds and removes some nameservers
            Given the domain has 3 nameservers
            When `domain.nameservers` is set to an array containing nameserver #1 plus
                two new nameservers
            Then `commands.CreateHost` is sent to create #4 and #5
            And `commands.UpdateDomain` is sent to add #4 and #5 plus remove #2 and #3
            And `commands.DeleteHost` is sent to delete #2 and #3
        """
        raise

    @skip("not implemented yet")
    def test_user_cannot_add_subordinate_without_ip(self):
        """
        Scenario: Registrant adds a nameserver which is a subdomain of their .gov
            Given the domain exists in the registry
            When `domain.nameservers` is set to an array containing an entry
                with a subdomain of the domain and no IP addresses
            Then Domain raises a user-friendly error
        """
        raise

    @skip("not implemented yet")
    def test_user_updates_ips(self):
        """
        Scenario: Registrant changes IP addresses for a nameserver
            Given the domain exists in the registry
            And has a subordinate nameserver
            When `domain.nameservers` is set to an array containing that nameserver
                with a different IP address(es)
            Then `commands.UpdateHost` is sent to the registry
        """
        raise

    @skip("not implemented yet")
    def test_user_cannot_add_non_subordinate_with_ip(self):
        """
        Scenario: Registrant adds a nameserver which is NOT a subdomain of their .gov
            Given the domain exists in the registry
            When `domain.nameservers` is set to an array containing an entry
                which is not a subdomain of the domain and has IP addresses
            Then Domain raises a user-friendly error
        """
        raise

    @skip("not implemented yet")
    def test_nameservers_are_idempotent(self):
        """
        Scenario: Registrant adds a set of nameservers twice, due to a UI glitch
            When `commands.CreateHost` and `commands.UpdateDomain` are sent
                to the registry twice with identical data
            Then no errors are raised in Domain
        """
        # implementation note: this requires seeing what happens when these are actually
        # sent like this, and then implementing appropriate mocks for any errors the
        # registry normally sends in this case
        raise

    @skip("not implemented yet")
    def test_update_is_unsuccessful(self):
        """
        Scenario: An update to the nameservers is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
        """
        raise


class TestRegistrantDNSSEC(TestCase):
    """Rule: Registrants may modify their secure DNS data"""

    def setUp(self):
        """
        Background:
            Given the registrant is logged in
            And the registrant is the admin on a domain
        """
        pass

    @skip("not implemented yet")
    def test_user_adds_dns_data(self):
        """
        Scenario: Registrant adds DNS data

        """
        raise

    @skip("not implemented yet")
    def test_dnssec_is_idempotent(self):
        """
        Scenario: Registrant adds DNS data twice, due to a UI glitch

        """
        # implementation note: this requires seeing what happens when these are actually
        # sent like this, and then implementing appropriate mocks for any errors the
        # registry normally sends in this case
        raise

    @skip("not implemented yet")
    def test_update_is_unsuccessful(self):
        """
        Scenario: An update to the dns data is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
        """
        raise


class TestAnalystClientHold(TestCase):
    """Rule: Analysts may suspend or restore a domain by using client hold"""

    def setUp(self):
        """
        Background:
            Given the analyst is logged in
            And a domain exists in the registry
        """
        pass

    @skip("not implemented yet")
    def test_analyst_places_client_hold(self):
        """
        Scenario: Analyst takes a domain off the internet
            When `domain.place_client_hold()` is called
            Then `CLIENT_HOLD` is added to the domain's statuses
        """
        raise

    @skip("not implemented yet")
    def test_analyst_places_client_hold_idempotent(self):
        """
        Scenario: Analyst tries to place client hold twice
            Given `CLIENT_HOLD` is already in the domain's statuses
            When `domain.place_client_hold()` is called
            Then Domain returns normally (without error)
        """
        raise

    @skip("not implemented yet")
    def test_analyst_removes_client_hold(self):
        """
        Scenario: Analyst restores a suspended domain
            Given `CLIENT_HOLD` is in the domain's statuses
            When `domain.remove_client_hold()` is called
            Then `CLIENT_HOLD` is no longer in the domain's statuses
        """
        raise

    @skip("not implemented yet")
    def test_analyst_removes_client_hold_idempotent(self):
        """
        Scenario: Analyst tries to remove client hold twice
            Given `CLIENT_HOLD` is not in the domain's statuses
            When `domain.remove_client_hold()` is called
            Then Domain returns normally (without error)
        """
        raise

    @skip("not implemented yet")
    def test_update_is_unsuccessful(self):
        """
        Scenario: An update to place or remove client hold is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
        """
        raise


class TestAnalystLock(TestCase):
    """Rule: Analysts may lock or unlock a domain to prevent or allow updates"""

    def setUp(self):
        """
        Background:
            Given the analyst is logged in
            And a domain exists in the registry
        """
        pass

    @skip("not implemented yet")
    def test_analyst_locks_domain(self):
        """
        Scenario: Analyst locks a domain to prevent edits or deletion
            When `domain.lock()` is called
            Then `CLIENT_DELETE_PROHIBITED` is added to the domain's statuses
            And `CLIENT_TRANSFER_PROHIBITED` is added to the domain's statuses
            And `CLIENT_UPDATE_PROHIBITED` is added to the domain's statuses
        """
        raise

    @skip("not implemented yet")
    def test_analyst_locks_domain_idempotent(self):
        """
        Scenario: Analyst tries to lock a domain twice
            Given `CLIENT_*_PROHIBITED` is already in the domain's statuses
            When `domain.lock()` is called
            Then Domain returns normally (without error)
        """
        raise

    @skip("not implemented yet")
    def test_analyst_removes_lock(self):
        """
        Scenario: Analyst unlocks a domain to allow deletion or edits
            Given `CLIENT_*_PROHIBITED` is in the domain's statuses
            When `domain.unlock()` is called
            Then `CLIENT_DELETE_PROHIBITED` is no longer in the domain's statuses
            And `CLIENT_TRANSFER_PROHIBITED` is no longer in the domain's statuses
            And `CLIENT_UPDATE_PROHIBITED` is no longer in the domain's statuses
        """
        raise

    @skip("not implemented yet")
    def test_analyst_removes_lock_idempotent(self):
        """
        Scenario: Analyst tries to unlock a domain twice
            Given `CLIENT_*_PROHIBITED` is not in the domain's statuses
            When `domain.unlock()` is called
            Then Domain returns normally (without error)
        """
        raise

    @skip("not implemented yet")
    def test_update_is_unsuccessful(self):
        """
        Scenario: An update to lock or unlock a domain is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
        """
        raise


class TestAnalystDelete(TestCase):
    """Rule: Analysts may delete a domain"""

    def setUp(self):
        """
        Background:
            Given the analyst is logged in
            And a domain exists in the registry
        """
        pass

    @skip("not implemented yet")
    def test_analyst_deletes_domain(self):
        """
        Scenario: Analyst permanently deletes a domain
            When `domain.delete()` is called
            Then `commands.DeleteDomain` is sent to the registry
            And `state` is set to `DELETED`
        """
        raise

    @skip("not implemented yet")
    def test_analyst_deletes_domain_idempotent(self):
        """
        Scenario: Analyst tries to delete an already deleted domain
            Given `state` is already `DELETED`
            When `domain.delete()` is called
            Then `commands.DeleteDomain` is sent to the registry
            And Domain returns normally (without error)
        """
        raise

    @skip("not implemented yet")
    def test_deletion_is_unsuccessful(self):
        """
        Scenario: Domain deletion is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
            And `state` is not set to `DELETED`
        """
        raise
