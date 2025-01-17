from itertools import zip_longest
import logging
import ipaddress
import re
from datetime import date, timedelta
from typing import Optional
from django.db import transaction
from django_fsm import FSMField, transition, TransitionNotAllowed  # type: ignore
from django.db import models, IntegrityError
from django.utils import timezone
from typing import Any
from registrar.models.host import Host
from registrar.models.host_ip import HostIP
from registrar.utility.enums import DefaultEmail
from registrar.utility import errors

from registrar.utility.errors import (
    ActionNotAllowed,
    NameserverError,
    NameserverErrorCodes as nsErrorCodes,
)

from epplibwrapper import (
    CLIENT as registry,
    commands,
    common as epp,
    extensions,
    info as eppInfo,
    RegistryError,
    ErrorCode,
)

from registrar.models.utility.contact_error import ContactError, ContactErrorCodes

from django.db.models import DateField, TextField
from .utility.domain_field import DomainField
from .utility.domain_helper import DomainHelper
from .utility.time_stamped_model import TimeStampedModel

from .public_contact import PublicContact

from .user_domain_role import UserDomainRole
from waffle.decorators import flag_is_active

logger = logging.getLogger(__name__)


class Domain(TimeStampedModel, DomainHelper):
    """
    Manage the lifecycle of domain names.

    The registry is the source of truth for this data and this model exists:
        1. To tie ownership information in the registrar to
           DNS entries in the registry

    ~~~ HOW TO USE THIS CLASS ~~~

    A) You can create a Domain object with just a name. `Domain(name="something.gov")`.
    B) Saving the Domain object will not contact the registry, as it may be useful
       to have Domain objects in an `UNKNOWN` pre-created state.
    C) Domain properties are lazily loaded. Accessing `my_domain.expiration_date` will
       contact the registry, if a cached copy does not exist.
    D) Domain creation is lazy. If `my_domain.expiration_date` finds that `my_domain`
       does not exist in the registry, it will ask the registry to create it.
    F) Created is _not_ the same as active aka live on the internet.
    G) Activation is controlled by the registry. It will happen automatically when the
       domain meets the required checks.
    """

    class Meta:
        """Contains meta information about this class"""

        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["state"]),
        ]

    def __init__(self, *args, **kwargs):
        self._cache = {}
        super(Domain, self).__init__(*args, **kwargs)

    class Status(models.TextChoices):
        """
        The status codes we can receive from the registry.

        These are detailed in RFC 5731 in section 2.3.
        https://www.rfc-editor.org/std/std69.txt
        """

        # Requests to delete the object MUST be rejected.
        CLIENT_DELETE_PROHIBITED = "clientDeleteProhibited"
        SERVER_DELETE_PROHIBITED = "serverDeleteProhibited"

        # DNS delegation information MUST NOT be published for the object.
        CLIENT_HOLD = "clientHold"
        SERVER_HOLD = "serverHold"

        # Requests to renew the object MUST be rejected.
        CLIENT_RENEW_PROHIBITED = "clientRenewProhibited"
        SERVER_RENEW_PROHIBITED = "serverRenewProhibited"

        # Requests to transfer the object MUST be rejected.
        CLIENT_TRANSFER_PROHIBITED = "clientTransferProhibited"
        SERVER_TRANSFER_PROHIBITED = "serverTransferProhibited"

        # Requests to update the object (other than to remove this status)
        # MUST be rejected.
        CLIENT_UPDATE_PROHIBITED = "clientUpdateProhibited"
        SERVER_UPDATE_PROHIBITED = "serverUpdateProhibited"

        # Delegation information has not been associated with the object.
        # This is the default status when a domain object is first created
        # and there are no associated host objects for the DNS delegation.
        # This status can also be set by the server when all host-object
        # associations are removed.
        INACTIVE = "inactive"

        # This is the normal status value for an object that has no pending
        # operations or prohibitions.  This value is set and removed by the
        # server as other status values are added or removed.
        OK = "ok"

        # A transform command has been processed for the object, but the
        # action has not been completed by the server.  Server operators can
        # delay action completion for a variety of reasons, such as to allow
        # for human review or third-party action.  A transform command that
        # is processed, but whose requested action is pending, is noted with
        # response code 1001.
        PENDING_CREATE = "pendingCreate"
        PENDING_DELETE = "pendingDelete"
        PENDING_RENEW = "pendingRenew"
        PENDING_TRANSFER = "pendingTransfer"
        PENDING_UPDATE = "pendingUpdate"

    class State(models.TextChoices):
        """These capture (some of) the states a domain object can be in."""

        # the state is indeterminate
        UNKNOWN = "unknown", "Unknown"

        # The domain object exists in the registry
        # but nameservers don't exist for it yet
        DNS_NEEDED = "dns needed", "Dns needed"

        # Domain has had nameservers set, may or may not be active
        READY = "ready", "Ready"

        # Registrar manually changed state to client hold
        ON_HOLD = "on hold", "On hold"

        # previously existed but has been deleted from the registry
        DELETED = "deleted", "Deleted"

        @classmethod
        def get_state_label(cls, state: str):
            """Returns the associated label for a given state value"""
            return cls(state).label if state else None

        @classmethod
        def get_help_text(cls, state) -> str:
            """Returns a help message for a desired state. If none is found, an empty string is returned"""
            help_texts = {
                # For now, unknown has the same message as DNS_NEEDED
                cls.UNKNOWN: ("Before this domain can be used, " "you’ll need to add name server addresses."),
                cls.DNS_NEEDED: ("Before this domain can be used, " "you’ll need to add name server addresses."),
                cls.READY: "This domain has name servers and is ready for use.",
                cls.ON_HOLD: (
                    "This domain is administratively paused, "
                    "so it can’t be edited and won’t resolve in DNS. "
                    "Contact help@get.gov for details."
                ),
                cls.DELETED: ("This domain has been removed and " "is no longer registered to your organization."),
            }

            return help_texts.get(state, "")

        @classmethod
        def get_admin_help_text(cls, state):
            """Returns a help message for a desired state for /admin. If none is found, an empty string is returned"""
            admin_help_texts = {
                cls.UNKNOWN: (
                    "The creator of the associated domain request has not logged in to "
                    "manage the domain since it was approved. "
                    'The state will switch to "DNS needed" after they access the domain in the registrar.'
                ),
                cls.DNS_NEEDED: (
                    "Before this domain can be used, name server addresses need to be added within the registrar."
                ),
                cls.READY: "This domain has name servers and is ready for use.",
                cls.ON_HOLD: (
                    "While on hold, this domain won't resolve in DNS and "
                    "any infrastructure (like websites) will be offline."
                ),
                cls.DELETED: (
                    "This domain was permanently removed from the registry. "
                    "The domain no longer resolves in DNS and any infrastructure (like websites) is offline."
                ),
            }

            return admin_help_texts.get(state, "")

    class Cache(property):
        """
        Python descriptor to turn class methods into properties.

        The purpose of subclassing `property` rather than using it directly
        as a decorator (`@Cache`) is to insert generic code to run
        before or after _all_ properties are accessed, modified, or deleted.

        As an example:

                domain = Domain(name="example.gov")
                domain.save()
                                      <--- insert code here
                date = domain.creation_date
                                      <--- or here
                (...other stuff...)
        """

        def __get__(self, obj, objtype=None):
            """Called during get. Example: `r = domain.registrant`."""
            return super().__get__(obj, objtype)

        def __set__(self, obj, value):
            """Called during set. Example: `domain.registrant = 'abc123'`."""
            super().__set__(obj, value)
            # always invalidate cache after sending updates to the registry
            obj._invalidate_cache()

        def __delete__(self, obj):
            """Called during delete. Example: `del domain.registrant`."""
            super().__delete__(obj)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        # If the domain is deleted we don't want the expiration date to be set
        if self.state == self.State.DELETED and self.expiration_date:
            self.expiration_date = None
        super().save(force_insert, force_update, using, update_fields)

    @classmethod
    def available(cls, domain: str) -> bool:
        """Check if a domain is available.
        This is called by the availablility api and
        is called in the validate function on the request/domain page

        throws- RegistryError or InvalidDomainError"""
        if not cls.string_could_be_domain(domain):
            logger.warning("Not a valid domain: %s" % str(domain))
            # throw invalid domain error so that it can be caught in
            # validate_and_handle_errors in domain_helper
            raise errors.InvalidDomainError()

        domain_name = domain.lower()
        req = commands.CheckDomain([domain_name])
        return registry.send(req, cleaned=True).res_data[0].avail

    @classmethod
    def registered(cls, domain: str) -> bool:
        """Check if a domain is _not_ available."""
        return not cls.available(domain)

    @Cache
    def registry_contacts(self) -> dict[str, str]:
        """
        Get a dictionary of registry IDs for the contacts for this domain.

        IDs are provided as strings, e.g.

            { PublicContact.ContactTypeChoices.REGISTRANT: "jd1234",
              PublicContact.ContactTypeChoices.ADMINISTRATIVE: "sh8013",...}
        """
        raise NotImplementedError()

    @Cache
    def creation_date(self) -> date:
        """Get the `cr_date` element from the registry."""
        return self._get_property("cr_date")

    @creation_date.setter  # type: ignore
    def creation_date(self, cr_date: date):
        """
        Direct setting of the creation date in the registry is not implemented.

        Creation date can only be set by registry."""
        raise NotImplementedError()

    @Cache
    def last_transferred_date(self) -> date:
        """Get the `tr_date` element from the registry."""
        raise NotImplementedError()

    @Cache
    def last_updated_date(self) -> date:
        """Get the `up_date` element from the registry."""
        return self._get_property("up_date")

    @Cache
    def registry_expiration_date(self) -> date:
        """Get or set the `ex_date` element from the registry.
        Additionally, _get_property updates the expiration date in the registrar"""
        try:
            return self._get_property("ex_date")
        except Exception as e:
            # exception raised during the save to registrar
            logger.error(f"error updating expiration date in registrar: {e}")
            raise (e)

    @registry_expiration_date.setter  # type: ignore
    def registry_expiration_date(self, ex_date: date):
        """
        Direct setting of the expiration date in the registry is not implemented.

        To update the expiration date, use renew_domain method."""
        raise NotImplementedError()

    def renew_domain(self, length: int = 1, unit: epp.Unit = epp.Unit.YEAR):
        """
        Renew the domain to a length and unit of time relative to the current
        expiration date.

        Default length and unit of time are 1 year.
        """

        # If no date is specified, grab the registry_expiration_date
        try:
            exp_date = self.registry_expiration_date
        except KeyError:
            # if no expiration date from registry, set it to today
            logger.warning("current expiration date not set; setting to today", exc_info=True)
            exp_date = date.today()
        # create RenewDomain request
        request = commands.RenewDomain(name=self.name, cur_exp_date=exp_date, period=epp.Period(length, unit))

        try:
            # update expiration date in registry, and set the updated
            # expiration date in the registrar, and in the cache
            self._cache["ex_date"] = registry.send(request, cleaned=True).res_data[0].ex_date
            self.expiration_date = self._cache["ex_date"]
            self.save()

        except RegistryError as err:
            # if registry error occurs, log the error, and raise it as well
            logger.error(f"Registry error renewing domain '{self.name}': {err}")
            raise (err)
        except Exception as e:
            # exception raised during the save to registrar
            logger.error(f"Error updating expiration date for domain '{self.name}' in registrar: {e}")
            raise (e)

    @Cache
    def password(self) -> str:
        """
        Get the `auth_info.pw` element from the registry. Not a real password.

        This `auth_info` element is required by the EPP protocol, but the registry is
        using a different mechanism to ensure unauthorized clients cannot perform
        actions on domains they do not own. This field provides no security features.
        It is not a secret.
        """
        raise NotImplementedError()

    @Cache
    def nameservers(self) -> list[tuple[str, list]]:
        """
        Get or set a complete list of nameservers for this domain.

        Hosts are provided as a list of tuples, e.g.

            [("ns1.example.com",), ("ns1.example.gov", ["0.0.0.0"])]

        Subordinate hosts (something.your-domain.gov) MUST have IP addresses,
        while non-subordinate hosts MUST NOT.
        """
        try:
            # attempt to retrieve hosts from registry and store in cache and db
            hosts = self._get_property("hosts")
        except Exception:
            # If exception raised returning hosts from registry, get from db
            hosts = []
            for hostobj in self.host.all():
                host_name = hostobj.name
                ips = [ip.address for ip in hostobj.ip.all()]
                hosts.append({"name": host_name, "addrs": ips})

        # TODO-687 fix this return value
        hostList = []
        for host in hosts:
            hostList.append((host["name"], host["addrs"]))
        return hostList

    def _create_host(self, host, addrs):
        """Creates the host object in the registry
        doesn't add the created host to the domain
        returns ErrorCode (int)"""
        if addrs is not None and addrs != []:
            addresses = [epp.Ip(addr=addr, ip="v6" if self.is_ipv6(addr) else None) for addr in addrs]
            request = commands.CreateHost(name=host, addrs=addresses)
        else:
            request = commands.CreateHost(name=host)

        try:
            logger.info("_create_host()-> sending req as %s" % request)
            response = registry.send(request, cleaned=True)
            return response.code
        except RegistryError as e:
            logger.error("Error _create_host, code was %s error was %s" % (e.code, e))
            # OBJECT_EXISTS is an expected error code that should not raise
            # an exception, rather return the code to be handled separately
            if e.code == ErrorCode.OBJECT_EXISTS:
                return e.code
            else:
                raise e

    def _convert_list_to_dict(self, listToConvert: list[tuple[str, list]]):
        """converts a list of hosts into a dictionary
        Args:
            list[tuple[str, list]]: such as [("123",["1","2","3"])]
            This is the list of hosts to convert

        returns:
            convertDict (dict(str,list))- such as{"123":["1","2","3"]}"""
        newDict: dict[str, Any] = {}

        for tup in listToConvert:
            if len(tup) == 1:
                newDict[tup[0]] = None
            elif len(tup) == 2:
                newDict[tup[0]] = tup[1]
        return newDict

    @classmethod
    def isSubdomain(cls, name: str, nameserver: str):
        """Returns boolean if the domain name is found in the argument passed"""
        subdomain_pattern = r"([\w-]+\.)*"
        full_pattern = subdomain_pattern + name
        regex = re.compile(full_pattern)
        return bool(regex.match(nameserver))

    @classmethod
    def isValidHost(cls, nameserver: str):
        """Checks for validity of nameserver string based on these rules:
        - first character is alpha or digit
        - first and last character in each label is alpha or digit
        - all characters alpha (lowercase), digit, -, or .
        - each label has a min length of 1 and a max length of 63
        - total host name has a max length of 253
        """
        # pattern to test for valid domain
        # label pattern for each section of the host name, separated by .
        labelpattern = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
        # lookahead pattern ensures first character not - and total length < 254
        lookaheadpatterns = r"^((?!-))(?=.{1,253}\.?$)"
        # pattern assembles lookaheadpatterns and ensures there are at least
        # 3 labels in the host name
        pattern = lookaheadpatterns + labelpattern + r"(\." + labelpattern + r"){2,}$"

        # attempt to match the pattern
        match = re.match(pattern, nameserver)

        # return true if nameserver matches
        # otherwise false
        return bool(match)

    @classmethod
    def checkHostIPCombo(cls, name: str, nameserver: str, ip: list[str]):
        """Checks the parameters past for a valid combination
        raises error if:
            - nameserver is a subdomain but is missing ip
            - nameserver is not a subdomain but has ip
            - nameserver is a subdomain but an ip passed is invalid
            - nameserver is not a valid domain
            - ip is provided but is missing domain

        Args:
            hostname (str)- nameserver or subdomain
            ip (list[str])-list of ip strings
        Throws:
            NameserverError (if exception hit)
        Returns:
            None"""
        if ip and not nameserver:
            raise NameserverError(code=nsErrorCodes.MISSING_HOST)
        elif nameserver and not cls.isValidHost(nameserver):
            raise NameserverError(code=nsErrorCodes.INVALID_HOST, nameserver=nameserver)
        elif cls.isSubdomain(name, nameserver) and (ip is None or ip == []):
            raise NameserverError(code=nsErrorCodes.MISSING_IP, nameserver=nameserver)
        elif not cls.isSubdomain(name, nameserver) and (ip is not None and ip != []):
            raise NameserverError(code=nsErrorCodes.GLUE_RECORD_NOT_ALLOWED, nameserver=nameserver, ip=ip)
        elif ip is not None and ip != []:
            for addr in ip:
                if not cls._valid_ip_addr(addr):
                    raise NameserverError(code=nsErrorCodes.INVALID_IP, nameserver=nameserver[:40], ip=ip)
        return None

    @classmethod
    def _valid_ip_addr(cls, ipToTest: str):
        """returns boolean if valid ip address string
        We currently only accept v4 or v6 ips
        returns:
            isValid (boolean)-True for valid ip address"""
        try:
            ip = ipaddress.ip_address(ipToTest)
            return ip.version == 6 or ip.version == 4

        except ValueError:
            return False

    def getNameserverChanges(self, hosts: list[tuple[str, list]]) -> tuple[list, list, dict, dict]:
        """
        calls self.nameserver, it should pull from cache but may result
        in an epp call
        Args:
            hosts: list[tuple[str, list]] such as [("123",["1","2","3"])]
        Throws:
            NameserverError (if exception hit)
        Returns:
            tuple[list, list, dict, dict]
                These four tuple values as follows:
                deleted_values: list[str]
                updated_values: list[str]
                new_values: dict(str,list)
                prevHostDict: dict(str,list)"""

        oldNameservers = self.nameservers

        previousHostDict = self._convert_list_to_dict(oldNameservers)

        newHostDict = self._convert_list_to_dict(hosts)
        deleted_values = []
        # TODO-currently a list of tuples, why not dict? for consistency
        updated_values = []
        new_values = {}

        for prevHost in previousHostDict:
            addrs = previousHostDict[prevHost]
            # get deleted values-which are values in previous nameserver list
            # but are not in the list of new host values
            if prevHost not in newHostDict:
                deleted_values.append(prevHost)
            # if the host exists in both, check if the addresses changed
            else:
                # TODO - host is being updated when previous was None+new is empty list
                # add check here
                if newHostDict[prevHost] is not None and set(newHostDict[prevHost]) != set(addrs):
                    self.__class__.checkHostIPCombo(name=self.name, nameserver=prevHost, ip=newHostDict[prevHost])
                    updated_values.append((prevHost, newHostDict[prevHost]))

        new_values = {
            key: newHostDict.get(key) for key in newHostDict if key not in previousHostDict and key.strip() != ""
        }

        for nameserver, ip in new_values.items():
            self.__class__.checkHostIPCombo(name=self.name, nameserver=nameserver, ip=ip)

        return (deleted_values, updated_values, new_values, previousHostDict)

    def _update_host_values(self, updated_values, oldNameservers):
        for hostTuple in updated_values:
            updated_response_code = self._update_host(hostTuple[0], hostTuple[1], oldNameservers.get(hostTuple[0]))
            if updated_response_code not in [
                ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
                ErrorCode.OBJECT_EXISTS,
            ]:
                logger.warning("Could not update host %s. Error code was: %s " % (hostTuple[0], updated_response_code))

    def createNewHostList(self, new_values: dict):
        """convert the dictionary of new values to a list of HostObjSet
        for use in the UpdateDomain epp message
        Args:
            new_values: dict(str,list)- dict of {nameserver:ips} to add to domain
        Returns:
            tuple [list[epp.HostObjSet], int]
            list[epp.HostObjSet]-epp object  for use in the UpdateDomain epp message
                defaults to empty list
            int-number of items being created default 0
        """

        hostStringList = []
        for key, value in new_values.items():
            createdCode = self._create_host(host=key, addrs=value)  # creates in registry
            if createdCode == ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY or createdCode == ErrorCode.OBJECT_EXISTS:
                hostStringList.append(key)
        if hostStringList == []:
            return [], 0

        addToDomainObject = epp.HostObjSet(hosts=hostStringList)
        return [addToDomainObject], len(hostStringList)

    def createDeleteHostList(self, hostsToDelete: list[str]):
        """
        Args:
            hostsToDelete (list[str])- list of nameserver/host names to remove
        Returns:
            tuple [list[epp.HostObjSet], int]
            list[epp.HostObjSet]-epp object  for use in the UpdateDomain epp message
                defaults to empty list
            int-number of items being created default 0
        """
        deleteStrList = []
        for nameserver in hostsToDelete:
            deleteStrList.append(nameserver)
        if deleteStrList == []:
            return [], 0
        deleteObj = epp.HostObjSet(hosts=hostsToDelete)

        return [deleteObj], len(deleteStrList)

    @Cache
    def dnssecdata(self) -> Optional[extensions.DNSSECExtension]:
        """
        Get a complete list of dnssecdata extensions for this domain.

        dnssecdata are provided as a list of DNSSECExtension objects.

        A DNSSECExtension object includes:
            maxSigLife: Optional[int]
            dsData: Optional[Sequence[DSData]]
            keyData: Optional[Sequence[DNSSECKeyData]]

        """
        try:
            return self._get_property("dnssecdata")
        except Exception as err:
            # Don't throw error as this is normal for a new domain
            logger.info("Domain does not have dnssec data defined %s" % err)
            return None

    def getDnssecdataChanges(self, _dnssecdata: Optional[extensions.DNSSECExtension]) -> tuple[dict, dict]:
        """
        calls self.dnssecdata, it should pull from cache but may result
        in an epp call
        returns tuple of 2 values as follows:
            addExtension: dict
            remExtension: dict

        addExtension includes all dsData to be added
        remExtension includes all dsData to be removed

        method operates on dsData;
        if dsData is not present, addExtension will be empty dict, and
        remExtension will be all existing dnssecdata to be deleted
        """

        oldDnssecdata = self.dnssecdata
        addDnssecdata: dict = {}
        remDnssecdata: dict = {}

        if _dnssecdata and _dnssecdata.dsData is not None:
            # initialize addDnssecdata and remDnssecdata for dsData
            addDnssecdata["dsData"] = _dnssecdata.dsData

            if oldDnssecdata and len(oldDnssecdata.dsData) > 0:
                # if existing dsData not in new dsData, mark for removal
                dsDataForRemoval = [dsData for dsData in oldDnssecdata.dsData if dsData not in _dnssecdata.dsData]
                if len(dsDataForRemoval) > 0:
                    remDnssecdata["dsData"] = dsDataForRemoval

                # if new dsData not in existing dsData, mark for add
                dsDataForAdd = [dsData for dsData in _dnssecdata.dsData if dsData not in oldDnssecdata.dsData]
                if len(dsDataForAdd) > 0:
                    addDnssecdata["dsData"] = dsDataForAdd
                else:
                    addDnssecdata["dsData"] = None

        else:
            # there are no new dsData, remove all dsData from existing
            remDnssecdata["dsData"] = getattr(oldDnssecdata, "dsData", None)

        return addDnssecdata, remDnssecdata

    @dnssecdata.setter  # type: ignore
    def dnssecdata(self, _dnssecdata: Optional[extensions.DNSSECExtension]):
        _addDnssecdata, _remDnssecdata = self.getDnssecdataChanges(_dnssecdata)
        addParams = {
            "maxSigLife": _addDnssecdata.get("maxSigLife", None),
            "dsData": _addDnssecdata.get("dsData", None),
        }
        remParams = {
            "maxSigLife": _remDnssecdata.get("maxSigLife", None),
            "remDsData": _remDnssecdata.get("dsData", None),
        }
        addRequest = commands.UpdateDomain(name=self.name)
        addExtension = commands.UpdateDomainDNSSECExtension(**addParams)
        addRequest.add_extension(addExtension)
        remRequest = commands.UpdateDomain(name=self.name)
        remExtension = commands.UpdateDomainDNSSECExtension(**remParams)
        remRequest.add_extension(remExtension)
        dsdata_change_log = ""

        # Get the user's email
        user_domain_role = UserDomainRole.objects.filter(domain=self).first()
        user_email = user_domain_role.user.email if user_domain_role else "unknown user"

        try:
            added_record = "dsData" in _addDnssecdata and _addDnssecdata["dsData"] is not None
            deleted_record = "dsData" in _remDnssecdata and _remDnssecdata["dsData"] is not None

            if added_record:
                registry.send(addRequest, cleaned=True)
                dsdata_change_log = f"{user_email} added a DS data record"
            if deleted_record:
                registry.send(remRequest, cleaned=True)
                if dsdata_change_log != "":  # if they add and remove a record at same time
                    dsdata_change_log = f"{user_email} added and deleted a DS data record"
                else:
                    dsdata_change_log = f"{user_email} deleted a DS data record"
            if dsdata_change_log != "":
                self.dsdata_last_change = dsdata_change_log
                self.save()  # audit log will now record this as a change

        except RegistryError as e:
            logger.error("Error updating DNSSEC, code was %s error was %s" % (e.code, e))
            raise e

    @nameservers.setter  # type: ignore
    def nameservers(self, hosts: list[tuple[str, list]]):  # noqa
        """Host should be a tuple of type str, str,... where the elements are
        Fully qualified host name, addresses associated with the host
        example: [(ns1.okay.gov, [127.0.0.1, others ips])]"""

        if len(hosts) > 13:
            raise NameserverError(code=nsErrorCodes.TOO_MANY_HOSTS)

        if self.state not in [
            self.State.DNS_NEEDED,
            self.State.READY,
            self.State.UNKNOWN,
        ]:
            raise ActionNotAllowed("Nameservers can not be " "set in the current state")

        logger.info("Setting nameservers")
        logger.info(hosts)

        # get the changes made by user and old nameserver values
        (
            deleted_values,
            updated_values,
            new_values,
            oldNameservers,
        ) = self.getNameserverChanges(hosts=hosts)

        _ = self._update_host_values(updated_values, oldNameservers)  # returns nothing, just need to be run and errors
        addToDomainList, addToDomainCount = self.createNewHostList(new_values)
        deleteHostList, deleteCount = self.createDeleteHostList(deleted_values)
        responseCode = self.addAndRemoveHostsFromDomain(hostsToAdd=addToDomainList, hostsToDelete=deleteHostList)

        # if unable to update domain raise error and stop
        if responseCode != ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY:
            raise NameserverError(code=nsErrorCodes.BAD_DATA)

        successTotalNameservers = len(oldNameservers) - deleteCount + addToDomainCount

        try:
            self._delete_hosts_if_not_used(hostsToDelete=deleted_values)
        except Exception as e:
            # we don't need this part to succeed in order to continue.
            logger.error("Failed to delete nameserver hosts: %s", e)

        if successTotalNameservers < 2:
            try:
                self.dns_needed()
                self.save()
            except Exception as err:
                logger.info("nameserver setter checked for dns_needed state and it did not succeed. Warning: %s" % err)
        elif successTotalNameservers >= 2 and successTotalNameservers <= 13:
            try:
                self.ready()
                self.save()
            except Exception as err:
                logger.info("nameserver setter checked for create state and it did not succeed. Warning: %s" % err)

    @Cache
    def statuses(self) -> list[str]:
        """
        Get the domain `status` elements from the registry.

        A domain's status indicates various properties. See Domain.Status.
        """
        try:
            return self._get_property("statuses")
        except KeyError:
            logger.error("Can't retrieve status from domain info")
            return []

    @statuses.setter  # type: ignore
    def statuses(self, statuses: list[str]):
        """
        We will not implement this. Statuses are set by the registry
        when we run delete and client hold, and these are the only statuses
        we will be triggering.
        """
        raise NotImplementedError()

    @Cache
    def registrant_contact(self) -> PublicContact | None:
        registrant = PublicContact.ContactTypeChoices.REGISTRANT
        return self.generic_contact_getter(registrant)

    @registrant_contact.setter  # type: ignore
    def registrant_contact(self, contact: PublicContact):
        """Registrant is set when a domain is created,
        so follow on additions will update the current registrant"""

        logger.info("making registrant contact")
        self._set_singleton_contact(contact=contact, expectedType=contact.ContactTypeChoices.REGISTRANT)

    @Cache
    def administrative_contact(self) -> PublicContact | None:
        """Get the admin contact for this domain."""
        admin = PublicContact.ContactTypeChoices.ADMINISTRATIVE
        return self.generic_contact_getter(admin)

    @administrative_contact.setter  # type: ignore
    def administrative_contact(self, contact: PublicContact):
        logger.info("making admin contact")
        self._set_singleton_contact(contact=contact, expectedType=contact.ContactTypeChoices.ADMINISTRATIVE)

    def _update_epp_contact(self, contact: PublicContact):
        """Sends UpdateContact to update the actual contact object,
        domain object remains unaffected
        should be used when changing email address
        or other contact info on an existing domain
        """
        updateContact = commands.UpdateContact(
            id=contact.registry_id,
            # type: ignore
            postal_info=self._make_epp_contact_postal_info(contact=contact),
            email=contact.email,
            voice=contact.voice,
            fax=contact.fax,
            auth_info=epp.ContactAuthInfo(pw="2fooBAR123fooBaz"),
        )  # type: ignore

        updateContact.disclose = self._disclose_fields(contact=contact)  # type: ignore
        try:
            registry.send(updateContact, cleaned=True)
        except RegistryError as e:
            logger.error("Error updating contact, code was %s error was %s" % (e.code, e))
            # TODO - ticket 433 human readable error handling here

    def _update_domain_with_contact(self, contact: PublicContact, rem=False):
        """adds or removes a contact from a domain
        rem being true indicates the contact will be removed from registry"""
        logger.info("_update_domain_with_contact() received type %s " % contact.contact_type)
        domainContact = epp.DomainContact(contact=contact.registry_id, type=contact.contact_type)

        updateDomain = commands.UpdateDomain(name=self.name, add=[domainContact])
        if rem:
            updateDomain = commands.UpdateDomain(name=self.name, rem=[domainContact])

        try:
            registry.send(updateDomain, cleaned=True)
        except RegistryError as e:
            logger.error("Error changing contact on a domain. Error code is %s error was %s" % (e.code, e))
            action = "add"
            if rem:
                action = "remove"

            raise Exception("Can't %s the contact of type %s" % (action, contact.contact_type))

    @Cache
    def security_contact(self) -> PublicContact | None:
        """Get or set the security contact for this domain."""
        security = PublicContact.ContactTypeChoices.SECURITY
        return self.generic_contact_getter(security)

    def _add_registrant_to_existing_domain(self, contact: PublicContact):
        """Used to change the registrant contact on an existing domain"""
        updateDomain = commands.UpdateDomain(name=self.name, registrant=contact.registry_id)
        try:
            registry.send(updateDomain, cleaned=True)
        except RegistryError as e:
            logger.error("Error changing to new registrant error code is %s, error is %s" % (e.code, e))
            # TODO-error handling better here?

    def _set_singleton_contact(self, contact: PublicContact, expectedType: str):  # noqa
        """Sets the contacts by adding them to the registry as new contacts,
        updates the contact if it is already in epp,
        deletes any additional contacts of the matching type for this domain
        does not create the PublicContact object, this should be made beforehand
        (call save() on a public contact to trigger the contact setters
        which inturn call this function)
        Will throw error if contact type is not the same as expectType
        Raises ValueError if expected type doesn't match the contact type"""
        if expectedType != contact.contact_type:
            raise ValueError("Cannot set a contact with a different contact type, expected type was %s" % expectedType)

        isRegistrant = contact.contact_type == contact.ContactTypeChoices.REGISTRANT
        isEmptySecurity = contact.contact_type == contact.ContactTypeChoices.SECURITY and contact.email == ""

        # get publicContact objects that have the matching
        # domain and type but a different id
        # like in highlander where there can only be one
        duplicate_contacts = PublicContact.objects.exclude(registry_id=contact.registry_id).filter(
            domain=self, contact_type=contact.contact_type
        )

        # if no record exists with this contact type
        # make contact in registry, duplicate and errors handled there
        errorCode = self._make_contact_in_registry(contact)

        # contact is already added to the domain, but something may have changed on it
        alreadyExistsInRegistry = errorCode == ErrorCode.OBJECT_EXISTS
        # if an error occured besides duplication, stop
        if not alreadyExistsInRegistry and errorCode != ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY:
            # TODO- ticket #433 look here for error handling
            raise RegistryError(code=errorCode)

        # contact doesn't exist on the domain yet
        logger.info("_set_singleton_contact()-> contact has been added to the registry")

        # if has conflicting contacts in our db remove them
        if duplicate_contacts.exists():
            logger.info("_set_singleton_contact()-> updating domain, removing old contact")

            existing_contact = duplicate_contacts.get()

            if isRegistrant:
                # send update domain only for registant contacts
                existing_contact.delete()
                self._add_registrant_to_existing_domain(contact)
            else:
                # remove the old contact and add a new one
                try:
                    self._update_domain_with_contact(contact=existing_contact, rem=True)
                    existing_contact.delete()
                except Exception as err:
                    logger.error("Raising error after removing and adding a new contact")
                    raise (err)

        # update domain with contact or update the contact itself
        if not isEmptySecurity:
            if not alreadyExistsInRegistry and not isRegistrant:
                self._update_domain_with_contact(contact=contact, rem=False)
            # if already exists just update
            elif alreadyExistsInRegistry:
                current_contact = PublicContact.objects.filter(registry_id=contact.registry_id).get()

                if current_contact.email != contact.email:
                    self._update_epp_contact(contact=contact)
        else:
            logger.info("removing security contact and setting default again")

            # get the current contact registry id for security
            current_contact = PublicContact.objects.filter(registry_id=contact.registry_id).get()

            # don't let user delete the default without adding a new email
            if current_contact.email != PublicContact.get_default_security().email:
                # remove the contact
                self._update_domain_with_contact(contact=current_contact, rem=True)
                current_contact.delete()
                # add new contact
                security_contact = self.get_default_security_contact()
                security_contact.save()

    @security_contact.setter  # type: ignore
    def security_contact(self, contact: PublicContact):
        """makes the contact in the registry,
        for security the public contact should have the org or registrant information
        from domain information (not domain request)
        and should have the security email from DomainRequest"""
        logger.info("making security contact in registry")
        self._set_singleton_contact(contact, expectedType=contact.ContactTypeChoices.SECURITY)

    @Cache
    def technical_contact(self) -> PublicContact | None:
        """Get or set the tech contact for this domain."""
        tech = PublicContact.ContactTypeChoices.TECHNICAL
        return self.generic_contact_getter(tech)

    @technical_contact.setter  # type: ignore
    def technical_contact(self, contact: PublicContact):
        logger.info("making technical contact")
        self._set_singleton_contact(contact, expectedType=contact.ContactTypeChoices.TECHNICAL)

    def is_active(self) -> bool:
        """Currently just returns if the state is created,
        because then it should be live, theoretically.
        Post mvp this should indicate
        Is the domain live on the inter webs?
        could be replaced with request to see if ok status is set
        """
        return self.state == self.State.READY

    def is_editable(self) -> bool:
        """domain is editable unless state is on hold or deleted"""
        return self.state in [
            self.State.UNKNOWN,
            self.State.DNS_NEEDED,
            self.State.READY,
        ]

    def transfer(self):
        """Going somewhere. Not implemented."""
        raise NotImplementedError()

    def renew(self):
        """Time to renew. Not implemented."""
        raise NotImplementedError()

    def get_security_email(self):
        logger.info("get_security_email-> getting the contact")

        security = PublicContact.ContactTypeChoices.SECURITY
        security_contact = self.generic_contact_getter(security)

        # If we get a valid value for security_contact, pull its email
        # Otherwise, just return nothing
        if security_contact is not None and isinstance(security_contact, PublicContact):
            return security_contact.email
        else:
            return None

    def clientHoldStatus(self):
        return epp.Status(state=self.Status.CLIENT_HOLD, description="", lang="en")

    def _place_client_hold(self):
        """This domain should not be active.
        may raises RegistryError, should be caught or handled correctly by caller"""
        request = commands.UpdateDomain(name=self.name, add=[self.clientHoldStatus()])
        try:
            registry.send(request, cleaned=True)
            self._invalidate_cache()
        except RegistryError as err:
            # if registry error occurs, log the error, and raise it as well
            logger.error(f"registry error placing client hold: {err}")
            raise (err)

    def _remove_client_hold(self):
        """This domain is okay to be active.
        may raises RegistryError, should be caught or handled correctly by caller"""
        request = commands.UpdateDomain(name=self.name, rem=[self.clientHoldStatus()])
        try:
            registry.send(request, cleaned=True)
            self._invalidate_cache()
        except RegistryError as err:
            # if registry error occurs, log the error, and raise it as well
            logger.error(f"registry error removing client hold: {err}")
            raise (err)

    def _delete_domain(self):
        """This domain should be deleted from the registry
        may raises RegistryError, should be caught or handled correctly by caller"""

        logger.info("Deleting subdomains for %s", self.name)
        # check if any subdomains are in use by another domain
        hosts = Host.objects.filter(name__regex=r".+{}".format(self.name))
        for host in hosts:
            if host.domain != self:
                logger.error("Unable to delete host: %s is in use by another domain: %s", host.name, host.domain)
                raise RegistryError(
                    code=ErrorCode.OBJECT_ASSOCIATION_PROHIBITS_OPERATION,
                    note=f"Host {host.name} is in use by {host.domain}",
                )

        (
            deleted_values,
            updated_values,
            new_values,
            oldNameservers,
        ) = self.getNameserverChanges(hosts=[])

        _ = self._update_host_values(updated_values, oldNameservers)  # returns nothing, just need to be run and errors
        addToDomainList, _ = self.createNewHostList(new_values)
        deleteHostList, _ = self.createDeleteHostList(deleted_values)
        responseCode = self.addAndRemoveHostsFromDomain(hostsToAdd=addToDomainList, hostsToDelete=deleteHostList)

        # if unable to update domain raise error and stop
        if responseCode != ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY:
            raise NameserverError(code=nsErrorCodes.BAD_DATA)

        # addAndRemoveHostsFromDomain removes the hosts from the domain object,
        # but we still need to delete the object themselves
        self._delete_hosts_if_not_used(hostsToDelete=deleted_values)

        logger.debug("Deleting non-registrant contacts for %s", self.name)
        contacts = PublicContact.objects.filter(domain=self)
        for contact in contacts:
            if contact.contact_type != PublicContact.ContactTypeChoices.REGISTRANT:
                self._update_domain_with_contact(contact, rem=True)
                request = commands.DeleteContact(contact.registry_id)
                registry.send(request, cleaned=True)

        logger.info("Deleting domain %s", self.name)
        request = commands.DeleteDomain(name=self.name)
        registry.send(request, cleaned=True)

    def __str__(self) -> str:
        return self.name

    name = DomainField(
        max_length=253,
        blank=False,
        default=None,  # prevent saving without a value
        unique=True,
        help_text="Fully qualified domain name",
        verbose_name="domain",
    )

    state = FSMField(
        max_length=21,
        choices=State.choices,
        default=State.UNKNOWN,
        # cannot change state directly, particularly in Django admin
        protected=True,
        # This must be defined for custom state help messages,
        # as otherwise the view will purge the help field as it does not exist.
        help_text=" ",
        verbose_name="domain state",
    )

    expiration_date = DateField(
        null=True,
        help_text=("Date the domain expires in the registry"),
    )

    security_contact_registry_id = TextField(
        null=True,
        help_text=("Duplication of registry's security contact id for when registry unavailable"),
        editable=False,
    )

    deleted = DateField(
        null=True,
        editable=False,
        help_text='Will appear blank unless the domain is in "deleted" state',
        verbose_name="deleted on",
    )

    first_ready = DateField(
        null=True,
        editable=False,
        help_text='Date when this domain first moved into "ready" state; date will never change',
        verbose_name="first ready on",
    )

    dsdata_last_change = TextField(
        null=True,
        blank=True,
        help_text="Record of the last change event for ds data",
    )

    def isActive(self):
        return self.state == Domain.State.CREATED

    def is_expired(self):
        """
        Check if the domain's expiration date is in the past.
        Returns True if expired, False otherwise.
        """
        if self.expiration_date is None:
            return self.state != self.State.DELETED
        now = timezone.now().date()
        return self.expiration_date < now

    def is_expiring(self):
        """
        Check if the domain's expiration date is within 60 days.
        Return True if domain expiration date exists and within 60 days
        and otherwise False bc there's no expiration date meaning so not expiring
        """
        if self.expiration_date is None:
            return False

        now = timezone.now().date()

        threshold_date = now + timedelta(days=60)
        return now < self.expiration_date <= threshold_date

    def state_display(self, request=None):
        """Return the display status of the domain."""
        if self.is_expired() and (self.state != self.State.UNKNOWN):
            return "Expired"
        elif flag_is_active(request, "domain_renewal") and self.is_expiring():
            return "Expiring soon"
        elif self.state == self.State.UNKNOWN or self.state == self.State.DNS_NEEDED:
            return "DNS needed"
        return self.state.capitalize()

    def map_epp_contact_to_public_contact(self, contact: eppInfo.InfoContactResultData, contact_id, contact_type):
        """Maps the Epp contact representation to a PublicContact object.

        contact -> eppInfo.InfoContactResultData: The converted contact object

        contact_id -> str: The given registry_id of the object (i.e "cheese@cia.gov")

        contact_type -> str: The given contact type, (i.e. "tech" or "registrant")
        """

        if contact is None:
            return None

        if contact_type is None:
            raise ContactError(code=ContactErrorCodes.CONTACT_TYPE_NONE)

        if contact_id is None:
            raise ContactError(code=ContactErrorCodes.CONTACT_ID_NONE)

        # Since contact_id is registry_id,
        # check that its the right length
        contact_id_length = len(contact_id)
        if contact_id_length > PublicContact.get_max_id_length() or contact_id_length < 1:
            raise ContactError(code=ContactErrorCodes.CONTACT_ID_INVALID_LENGTH)

        if not isinstance(contact, eppInfo.InfoContactResultData):
            raise ContactError(code=ContactErrorCodes.CONTACT_INVALID_TYPE)

        auth_info = contact.auth_info
        postal_info = contact.postal_info
        addr = postal_info.addr
        streets = None
        if addr is not None:
            streets = addr.street
        streets_kwargs = self._convert_streets_to_dict(streets)
        desired_contact = PublicContact(
            domain=self,
            contact_type=contact_type,
            registry_id=contact_id,
            email=contact.email or "",
            voice=contact.voice or "",
            fax=contact.fax,
            name=postal_info.name or "",
            org=postal_info.org,
            # For linter - default to "" instead of None
            pw=getattr(auth_info, "pw", ""),
            city=getattr(addr, "city", ""),
            pc=getattr(addr, "pc", ""),
            cc=getattr(addr, "cc", ""),
            sp=getattr(addr, "sp", ""),
            **streets_kwargs,
        )  # type: ignore

        return desired_contact

    def _convert_streets_to_dict(self, streets):
        """
        Converts EPPLibs street representation
        to PublicContacts.

        Args:
            streets (Sequence[str]): Streets from EPPLib.

        Returns:
            dict: {
                "street1": str or "",

                "street2": str or None,

                "street3": str or None,
            }

        EPPLib returns 'street' as an sequence of strings.
        Meanwhile, PublicContact has this split into three
        seperate properties: street1, street2, street3.

        Handles this disparity.
        """
        # 'zips' two lists together.
        # For instance, (('street1', 'some_value_here'),
        # ('street2', 'some_value_here'))
        # Dict then converts this to a useable kwarg which we can pass in
        street_dict = dict(
            zip_longest(
                ["street1", "street2", "street3"],
                streets if streets is not None else [""],
                fillvalue=None,
            )
        )
        return street_dict

    def _request_contact_info(self, contact: PublicContact):
        try:
            req = commands.InfoContact(id=contact.registry_id)
            return registry.send(req, cleaned=True).res_data[0]
        except RegistryError as error:
            logger.error(
                "Registry threw error for contact id %s contact type is %s, error code is\n %s full error is %s",  # noqa
                contact.registry_id,
                contact.contact_type,
                error.code,
                error,
            )
            raise error

    def generic_contact_getter(self, contact_type_choice: PublicContact.ContactTypeChoices) -> PublicContact | None:
        """Retrieves the desired PublicContact from the registry.
        This abstracts the caching and EPP retrieval for
        all contact items and thus may result in EPP calls being sent.

        contact_type_choice is a literal in PublicContact.ContactTypeChoices,
        for instance: PublicContact.ContactTypeChoices.SECURITY.

        If you wanted to setup getter logic for Security, you would call:
        cache_contact_helper(PublicContact.ContactTypeChoices.SECURITY),
        or cache_contact_helper("security").
        """
        # registrant_contact(s) are an edge case. They exist on
        # the "registrant" property as opposed to contacts.
        desired_property = "contacts"
        if contact_type_choice == PublicContact.ContactTypeChoices.REGISTRANT:
            desired_property = "registrant"

        try:
            # Grab from cache
            contacts = self._get_property(desired_property)
        except KeyError as error:
            # if contact type is security, attempt to retrieve registry id
            # for the security contact from domain.security_contact_registry_id
            if contact_type_choice == PublicContact.ContactTypeChoices.SECURITY and self.security_contact_registry_id:
                logger.info(f"Could not access registry, using fallback value of {self.security_contact_registry_id}")
                contacts = {PublicContact.ContactTypeChoices.SECURITY: self.security_contact_registry_id}
            else:
                logger.error(f"Could not find {contact_type_choice}: {error}")
                return None

        cached_contact = self.get_contact_in_keys(contacts, contact_type_choice)
        if cached_contact is None:
            # TODO - #1103
            raise ContactError("No contact was found in cache or the registry")

        return cached_contact

    def get_default_security_contact(self):
        """Gets the default security contact."""
        logger.info("get_default_security_contact() -> Adding default security contact")
        contact = PublicContact.get_default_security()
        contact.domain = self
        return contact

    def get_default_administrative_contact(self):
        """Gets the default administrative contact."""
        logger.info("get_default_administrative_contact() -> Adding administrative security contact")
        contact = PublicContact.get_default_administrative()
        contact.domain = self
        return contact

    def get_default_technical_contact(self):
        """Gets the default technical contact."""
        logger.info("get_default_security_contact() -> Adding technical security contact")
        contact = PublicContact.get_default_technical()
        contact.domain = self
        return contact

    def get_default_registrant_contact(self):
        """Gets the default registrant contact."""
        logger.info("get_default_security_contact() -> Adding default registrant contact")
        contact = PublicContact.get_default_registrant()
        contact.domain = self
        return contact

    def get_contact_in_keys(self, contacts, contact_type):
        """Gets a contact object.

        Args:
            contacts ([PublicContact]): List of PublicContacts
            contact_type (literal): Which PublicContact to get
        Returns:
            PublicContact | None
        """
        logger.info(f"get_contact_in_keys() -> Grabbing a {contact_type} contact from cache")
        # Registrant doesn't exist as an array, and is of
        # a special data type, so we need to handle that.
        if contact_type == PublicContact.ContactTypeChoices.REGISTRANT:
            desired_contact = None
            if isinstance(contacts, str):
                desired_contact = self._registrant_to_public_contact(self._cache["registrant"])
                # Set the cache with the updated object
                # for performance reasons.
                if "registrant" in self._cache:
                    self._cache["registrant"] = desired_contact
            elif isinstance(contacts, PublicContact):
                desired_contact = contacts

            return self._handle_registrant_contact(desired_contact)

        _registry_id: str = ""
        if contacts is not None and contact_type in contacts:
            _registry_id = contacts.get(contact_type)

        desired = PublicContact.objects.filter(registry_id=_registry_id, domain=self, contact_type=contact_type)

        if desired.count() == 1:
            return desired.get()

        logger.info(f"Requested contact {_registry_id} does not exist in cache.")
        return None

    def _handle_registrant_contact(self, contact):
        if contact.contact_type is not None and contact.contact_type == PublicContact.ContactTypeChoices.REGISTRANT:
            return contact
        else:
            raise ValueError("Invalid contact object for registrant_contact")

    # ForeignKey on UserDomainRole creates a "permissions" member for
    # all of the user-roles that are in place for this domain

    # ManyToManyField on User creates a "users" member for all of the
    # users who have some role on this domain

    # ForeignKey on DomainInvitation creates an "invitations" member for
    # all of the invitations that have been sent for this domain

    def _get_or_create_domain(self):
        """Try to fetch info about this domain. Create it if it does not exist."""
        already_tried_to_create = False
        exitEarly = False
        count = 0
        while not exitEarly and count < 3:
            try:
                req = commands.InfoDomain(name=self.name)
                domainInfoResponse = registry.send(req, cleaned=True)
                exitEarly = True
                return domainInfoResponse
            except RegistryError as e:
                count += 1

                if already_tried_to_create:
                    logger.error("Already tried to create")
                    logger.error(e)
                    logger.error(e.code)
                    raise e
                if e.code == ErrorCode.OBJECT_DOES_NOT_EXIST and self.state == Domain.State.UNKNOWN:
                    logger.info("_get_or_create_domain() -> Switching to dns_needed from unknown")
                    # avoid infinite loop
                    already_tried_to_create = True
                    self.dns_needed_from_unknown()
                    self.save()
                else:
                    logger.error(e)
                    logger.error(e.code)
                    raise e

    def addRegistrant(self):
        """Adds a default registrant contact"""
        registrant = PublicContact.get_default_registrant()
        registrant.domain = self
        registrant.save()  # calls the registrant_contact.setter
        return registrant.registry_id

    @transition(field="state", source=State.UNKNOWN, target=State.DNS_NEEDED)
    def dns_needed_from_unknown(self):
        logger.info("Changing to dns_needed")

        registrantID = self.addRegistrant()

        req = commands.CreateDomain(
            name=self.name,
            registrant=registrantID,
            auth_info=epp.DomainAuthInfo(pw="2fooBAR123fooBaz"),  # not a password
        )

        try:
            registry.send(req, cleaned=True)

        except RegistryError as err:
            if err.code != ErrorCode.OBJECT_EXISTS:
                raise err

        self.addAllDefaults()

    def addAllDefaults(self):
        """Adds default security, technical, and administrative contacts"""
        logger.info("addAllDefaults() -> Adding default security, technical, and administrative contacts")
        security_contact = self.get_default_security_contact()
        security_contact.save()

        technical_contact = self.get_default_technical_contact()
        technical_contact.save()

        administrative_contact = self.get_default_administrative_contact()
        administrative_contact.save()

    @transition(field="state", source=[State.READY, State.ON_HOLD], target=State.ON_HOLD)
    def place_client_hold(self, ignoreEPP=False):
        """place a clienthold on a domain (no longer should resolve)
        ignoreEPP (boolean) - set to true to by-pass EPP (used for transition domains)
        """

        # (check prohibited statuses)
        logger.info("clientHold()-> inside clientHold")

        # In order to allow transition domains to by-pass EPP calls,
        # include this ignoreEPP flag
        if not ignoreEPP:
            self._place_client_hold()

    @transition(field="state", source=[State.READY, State.ON_HOLD], target=State.READY)
    def revert_client_hold(self, ignoreEPP=False):
        """undo a clienthold placed on a domain
        ignoreEPP (boolean) - set to true to by-pass EPP (used for transition domains)
        """

        logger.info("clientHold()-> inside clientHold")
        if not ignoreEPP:
            self._remove_client_hold()
        # TODO -on the client hold ticket any additional error handling here

    @transition(field="state", source=[State.ON_HOLD, State.DNS_NEEDED], target=State.DELETED)
    def deletedInEpp(self):
        """Domain is deleted in epp but is saved in our database.
        Subdomains will be deleted first if not in use by another domain.
        Contacts for this domain will also be deleted.
        Error handling should be provided by the caller."""
        # While we want to log errors, we want to preserve
        # that information when this function is called.
        # Human-readable errors are introduced at the admin.py level,
        # as doing everything here would reduce reliablity.
        try:
            logger.info("deletedInEpp()-> inside _delete_domain")
            self._delete_domain()
            self.deleted = timezone.now()
            self.expiration_date = None
        except RegistryError as err:
            logger.error(f"Could not delete domain. Registry returned error: {err}. {err.note}")
            raise err
        except TransitionNotAllowed as err:
            logger.error("Could not delete domain. FSM failure: {err}")
            raise err
        except Exception as err:
            logger.error(f"Could not delete domain. An unspecified error occured: {err}")
            raise err
        else:
            self._invalidate_cache()

    # def is_dns_needed(self):
    #     """Commented out and kept in the codebase
    #     as this call should be made, but adds
    #     a lot of processing time
    #     when EPP calling is made more efficient
    #     this should be added back in

    #     The goal is to double check that
    #     the nameservers we set are in fact
    #     on the registry
    #     """
    #     self._invalidate_cache()
    #     nameserverList = self.nameservers
    #     return len(nameserverList) < 2

    # def dns_not_needed(self):
    #     return not self.is_dns_needed()

    @transition(
        field="state",
        source=[State.DNS_NEEDED, State.READY],
        target=State.READY,
        # conditions=[dns_not_needed]
    )
    def ready(self):
        """Transition to the ready state
        domain should have nameservers and all contacts
        and now should be considered live on a domain
        """
        logger.info("Changing to ready state")
        logger.info("able to transition to ready state")
        # if self.first_ready is not None, this means that this
        # domain was READY, then not READY, then is READY again.
        # We do not want to overwrite first_ready.
        if self.first_ready is None:
            self.first_ready = timezone.now()

    @transition(
        field="state",
        source=[State.READY],
        target=State.DNS_NEEDED,
        # conditions=[is_dns_needed]
    )
    def dns_needed(self):
        """Transition to the DNS_NEEDED state
        domain should NOT have nameservers but
        SHOULD have all contacts
        Going to check nameservers and will
        result in an EPP call
        """
        logger.info("Changing to DNS_NEEDED state")
        logger.info("able to transition to DNS_NEEDED state")

    def get_state_help_text(self, request=None) -> str:
        """Returns a str containing additional information about a given state.
        Returns custom content for when the domain itself is expired."""

        if self.is_expired() and self.state != self.State.UNKNOWN:
            # Given expired is not a physical state, but it is displayed as such,
            # We need custom logic to determine this message.
            help_text = (
                "This domain has expired, but it is still online. " "To renew this domain, contact help@get.gov."
            )
        elif flag_is_active(request, "domain_renewal") and self.is_expiring():
            help_text = "This domain will expire soon. Contact one of the listed domain managers to renew the domain."
        else:
            help_text = Domain.State.get_help_text(self.state)

        return help_text

    def _disclose_fields(self, contact: PublicContact):
        """creates a disclose object that can be added to a contact Create using
        .disclose= <this function> on the command before sending.
        if item is security email then make sure email is visible"""
        is_security = contact.contact_type == contact.ContactTypeChoices.SECURITY
        DF = epp.DiscloseField
        fields = {DF.EMAIL}

        hidden_security_emails = [DefaultEmail.PUBLIC_CONTACT_DEFAULT.value, DefaultEmail.LEGACY_DEFAULT.value]
        disclose = is_security and contact.email not in hidden_security_emails
        # Delete after testing on other devices
        logger.info("Updated domain contact %s to disclose: %s", contact.email, disclose)
        # Will only disclose DF.EMAIL if its not the default
        return epp.Disclose(
            flag=disclose,
            fields=fields,
        )

    def _make_epp_contact_postal_info(self, contact: PublicContact):  # type: ignore
        return epp.PostalInfo(  # type: ignore
            name=contact.name,
            addr=epp.ContactAddr(
                street=[
                    getattr(contact, street) for street in ["street1", "street2", "street3"] if hasattr(contact, street)
                ],  # type: ignore
                city=contact.city,
                pc=contact.pc,
                cc=contact.cc,
                sp=contact.sp,
            ),
            org=contact.org,
            type="loc",
        )

    def _make_contact_in_registry(self, contact: PublicContact):
        """Create the contact in the registry, ignore duplicate contact errors
        returns int corresponding to ErrorCode values"""

        create = commands.CreateContact(
            id=contact.registry_id,
            postal_info=self._make_epp_contact_postal_info(contact=contact),
            email=contact.email,
            voice=contact.voice,
            fax=contact.fax,
            auth_info=epp.ContactAuthInfo(pw="2fooBAR123fooBaz"),
        )  # type: ignore
        # security contacts should only show email addresses, for now
        create.disclose = self._disclose_fields(contact=contact)
        try:
            registry.send(create, cleaned=True)
            return ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY
        except RegistryError as err:
            # don't throw an error if it is just saying this is a duplicate contact
            if err.code != ErrorCode.OBJECT_EXISTS:
                logger.error(
                    "Registry threw error for contact id %s"
                    " contact type is %s,"
                    " error code is\n %s"
                    " full error is %s",
                    contact.registry_id,
                    contact.contact_type,
                    err.code,
                    err,
                )
                # TODO - 433 Error handling here

            else:
                logger.warning(
                    "Registrar tried to create duplicate contact for id %s",
                    contact.registry_id,
                )
            return err.code

    def _fetch_contacts(self, contact_data):
        """Fetch contact info."""
        choices = PublicContact.ContactTypeChoices
        # We expect that all these fields get populated,
        # so we can create these early, rather than waiting.
        contacts_dict = {
            choices.ADMINISTRATIVE: None,
            choices.SECURITY: None,
            choices.TECHNICAL: None,
        }
        for domainContact in contact_data:
            req = commands.InfoContact(id=domainContact.contact)
            data = registry.send(req, cleaned=True).res_data[0]
            logger.info(f"_fetch_contacts => this is the data: {data}")

            # Map the object we recieved from EPP to a PublicContact
            mapped_object = self.map_epp_contact_to_public_contact(data, domainContact.contact, domainContact.type)
            logger.info(f"_fetch_contacts => mapped_object: {mapped_object}")

            # Find/create it in the DB
            in_db = self._get_or_create_public_contact(mapped_object)
            contacts_dict[in_db.contact_type] = in_db.registry_id
        return contacts_dict

    def _get_or_create_contact(self, contact: PublicContact):
        """Try to fetch info about a contact. Create it if it does not exist."""
        logger.info("_get_or_create_contact() -> Fetching contact info")
        try:
            return self._request_contact_info(contact)
        except RegistryError as e:
            if e.code == ErrorCode.OBJECT_DOES_NOT_EXIST:
                logger.info("_get_or_create_contact()-> contact doesn't exist so making it")
                contact.domain = self
                contact.save()  # this will call the function based on type of contact
                return self._request_contact_info(contact=contact)
            else:
                logger.error(
                    "Registry threw error for contact id %s"
                    " contact type is %s,"
                    " error code is\n %s"
                    " full error is %s",
                    contact.registry_id,
                    contact.contact_type,
                    e.code,
                    e,
                )

                raise e

    def is_ipv6(self, ip: str):
        ip_addr = ipaddress.ip_address(ip)
        return ip_addr.version == 6

    def _fetch_hosts(self, host_data):
        """Fetch host info."""
        hosts = []
        for name in host_data:
            req = commands.InfoHost(name=name)
            data = registry.send(req, cleaned=True).res_data[0]
            host = {
                "name": name,
                "addrs": [item.addr for item in getattr(data, "addrs", [])],
                "cr_date": getattr(data, "cr_date", ...),
                "statuses": getattr(data, "statuses", ...),
                "tr_date": getattr(data, "tr_date", ...),
                "up_date": getattr(data, "up_date", ...),
            }
            hosts.append({k: v for k, v in host.items() if v is not ...})
        return hosts

    def _convert_ips(self, ip_list: list[str]):
        """Convert Ips to a list of epp.Ip objects
        use when sending update host command.
        if there are no ips an empty list will be returned

        Args:
            ip_list (list[str]): the new list of ips, may be empty
        Returns:
            edited_ip_list (list[epp.Ip]): list of epp.ip objects ready to
            be sent to the registry
        """
        edited_ip_list = []
        if ip_list is None:
            return []

        for ip_addr in ip_list:
            edited_ip_list.append(epp.Ip(addr=ip_addr, ip="v6" if self.is_ipv6(ip_addr) else None))

        return edited_ip_list

    def _update_host(self, nameserver: str, ip_list: list[str], old_ip_list: list[str]):
        """Update an existing host object in EPP. Sends the update host command
        can result in a RegistryError
        Args:
            nameserver (str): nameserver or subdomain
            ip_list (list[str]): the new list of ips, may be empty
            old_ip_list  (list[str]): the old ip list, may also be empty

        Returns:
            errorCode (int): one of ErrorCode enum type values

        """
        try:
            if ip_list is None or len(ip_list) == 0 and isinstance(old_ip_list, list) and len(old_ip_list) != 0:
                return ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY

            added_ip_list = set(ip_list).difference(old_ip_list)
            removed_ip_list = set(old_ip_list).difference(ip_list)

            request = commands.UpdateHost(
                name=nameserver,
                add=self._convert_ips(list(added_ip_list)),
                rem=self._convert_ips(list(removed_ip_list)),
            )
            response = registry.send(request, cleaned=True)
            logger.info("_update_host()-> sending req as %s" % request)
            return response.code
        except RegistryError as e:
            logger.error("Error _update_host, code was %s error was %s" % (e.code, e))
            # OBJECT_EXISTS is an expected error code that should not raise
            # an exception, rather return the code to be handled separately
            if e.code == ErrorCode.OBJECT_EXISTS:
                return e.code
            else:
                raise e

    def addAndRemoveHostsFromDomain(self, hostsToAdd: list[str], hostsToDelete: list[str]):
        """sends an UpdateDomain message to the registry with the hosts provided
        Args:
            hostsToDelete (list[epp.HostObjSet])- list of host objects to delete
            hostsToAdd (list[epp.HostObjSet])- list of host objects to add
        Returns:
            response code (int)- RegistryErrorCode integer value
            defaults to return COMMAND_COMPLETED_SUCCESSFULLY
            if there is nothing to add or delete
        """

        if hostsToAdd == [] and hostsToDelete == []:
            return ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY

        try:
            updateReq = commands.UpdateDomain(name=self.name, rem=hostsToDelete, add=hostsToAdd)

            logger.info("addAndRemoveHostsFromDomain()-> sending update domain req as %s" % updateReq)
            response = registry.send(updateReq, cleaned=True)

            return response.code
        except RegistryError as e:
            logger.error("Error addAndRemoveHostsFromDomain, code was %s error was %s" % (e.code, e))
            return e.code

    def _delete_hosts_if_not_used(self, hostsToDelete: list[str]):
        """delete the host object in registry,
        will only delete the host object, if it's not being used by another domain
        Performs just the DeleteHost epp call
        Args:
            hostsToDelete (list[str])- list of nameserver/host names to remove
        Returns:
            None

        """
        try:
            for nameserver in hostsToDelete:
                deleteHostReq = commands.DeleteHost(name=nameserver)
                registry.send(deleteHostReq, cleaned=True)
                logger.info("_delete_hosts_if_not_used()-> sending delete host req as %s" % deleteHostReq)

        except RegistryError as e:
            if e.code == ErrorCode.OBJECT_ASSOCIATION_PROHIBITS_OPERATION:
                logger.info("Did not remove host %s because it is in use on another domain." % nameserver)
            else:
                logger.error("Error _delete_hosts_if_not_used, code was %s error was %s" % (e.code, e))

            raise e

    def _fix_unknown_state(self, cleaned):
        """
        _fix_unknown_state: Calls _add_missing_contacts_if_unknown
        to add contacts in as needed (or return an error). Otherwise
        if we are able to add contacts and the state is out of UNKNOWN
        and (and should be into DNS_NEEDED), we double check the
        current state and # of nameservers and update the state from there
        """
        try:
            self._add_missing_contacts_if_unknown(cleaned)

        except Exception as e:
            logger.error(
                "%s couldn't _add_missing_contacts_if_unknown, error was %s."
                "Domain will still be in UNKNOWN state." % (self.name, e)
            )
        if len(self.nameservers) >= 2 and (self.state != self.State.READY):
            self.ready()
            self.save()

    @transition(field="state", source=State.UNKNOWN, target=State.DNS_NEEDED)
    def _add_missing_contacts_if_unknown(self, cleaned):
        """
        _add_missing_contacts_if_unknown: Add contacts (SECURITY, TECHNICAL, and/or ADMINISTRATIVE)
        if they are missing, AND switch the state to DNS_NEEDED from UNKNOWN (if it
        is in an UNKNOWN state, that is an error state)
        Note: The transition state change happens at the end of the function
        """

        missingAdmin = True
        missingSecurity = True
        missingTech = True

        contacts = cleaned.get("_contacts", [])
        if len(contacts) < 3:
            for contact in contacts:
                if contact.type == PublicContact.ContactTypeChoices.ADMINISTRATIVE:
                    missingAdmin = False
                if contact.type == PublicContact.ContactTypeChoices.SECURITY:
                    missingSecurity = False
                if contact.type == PublicContact.ContactTypeChoices.TECHNICAL:
                    missingTech = False

            # We are only creating if it doesn't exist so we don't overwrite
            if missingAdmin:
                administrative_contact = self.get_default_administrative_contact()
                administrative_contact.save()
            if missingSecurity:
                security_contact = self.get_default_security_contact()
                security_contact.save()
            if missingTech:
                technical_contact = self.get_default_technical_contact()
                technical_contact.save()

            logger.info(
                "_add_missing_contacts_if_unknown => Adding contacts. Values are "
                f"missingAdmin: {missingAdmin}, missingSecurity: {missingSecurity}, missingTech: {missingTech}"
            )

    def _fetch_cache(self, fetch_hosts=False, fetch_contacts=False):
        """Contact registry for info about a domain."""
        try:
            data_response = self._get_or_create_domain()
            cache = self._extract_data_from_response(data_response)
            cleaned = self._clean_cache(cache, data_response)
            self._update_hosts_and_contacts(cleaned, fetch_hosts, fetch_contacts)

            if self.state == self.State.UNKNOWN:
                self._fix_unknown_state(cleaned)
            if fetch_hosts:
                self._update_hosts_and_ips_in_db(cleaned)
            if fetch_contacts:
                self._update_security_contact_in_db(cleaned)
            self._update_dates(cleaned)

            self._cache = cleaned

        except RegistryError as e:
            logger.error(e)

    def _extract_data_from_response(self, data_response):
        """extract data from response from registry"""
        data = data_response.res_data[0]
        return {
            "auth_info": getattr(data, "auth_info", ...),
            "_contacts": getattr(data, "contacts", ...),
            "cr_date": getattr(data, "cr_date", ...),
            "ex_date": getattr(data, "ex_date", ...),
            "_hosts": getattr(data, "hosts", ...),
            "name": getattr(data, "name", ...),
            "registrant": getattr(data, "registrant", ...),
            "statuses": getattr(data, "statuses", ...),
            "tr_date": getattr(data, "tr_date", ...),
            "up_date": getattr(data, "up_date", ...),
        }

    def _clean_cache(self, cache, data_response):
        """clean up the cache"""
        # remove null properties (to distinguish between "a value of None" and null)
        cleaned = self._remove_null_properties(cache)
        if "statuses" in cleaned:
            cleaned["statuses"] = [status.state for status in cleaned["statuses"]]
        cleaned["dnssecdata"] = self._get_dnssec_data(data_response.extensions)
        return cleaned

    def _remove_null_properties(self, cache):
        return {k: v for k, v in cache.items() if v is not ...}

    def _get_dnssec_data(self, response_extensions):
        # get extensions info, if there is any
        # DNSSECExtension is one possible extension, make sure to handle
        # only DNSSECExtension and not other type extensions
        dnssec_data = None
        for extension in response_extensions:
            if isinstance(extension, extensions.DNSSECExtension):
                dnssec_data = extension
        return dnssec_data

    def _update_hosts_and_contacts(self, cleaned, fetch_hosts, fetch_contacts):
        """
        Update hosts and contacts if fetch_hosts and/or fetch_contacts.
        Additionally, capture and cache old hosts and contacts from cache if they
        don't exist in cleaned
        """
        old_cache_hosts = self._cache.get("hosts")
        old_cache_contacts = self._cache.get("contacts")

        if fetch_contacts:
            cleaned["contacts"] = self._get_contacts(cleaned.get("_contacts", []))
            if old_cache_hosts is not None:
                logger.debug("resetting cleaned['hosts'] to old_cache_hosts")
                cleaned["hosts"] = old_cache_hosts

        if fetch_hosts:
            cleaned["hosts"] = self._get_hosts(cleaned.get("_hosts", []))
            if old_cache_contacts is not None:
                cleaned["contacts"] = old_cache_contacts

    def _update_hosts_and_ips_in_db(self, cleaned):
        """Update hosts and host_ips in database if retrieved from registry.
        Only called when fetch_hosts is True.

        Parameters:
            self: the domain to be updated with hosts and ips from cleaned
            cleaned: dict containing hosts.  Hosts are provided as a list of dicts, e.g.
                [{"name": "ns1.example.com",}, {"name": "ns1.example.gov"}, "addrs": ["0.0.0.0"])]
        """
        cleaned_hosts = cleaned["hosts"]
        # Get all existing hosts from the database for this domain
        existing_hosts_in_db = Host.objects.filter(domain=self)
        # Identify hosts to delete
        cleaned_host_names = set(cleaned_host["name"] for cleaned_host in cleaned_hosts)
        hosts_to_delete_from_db = [
            existing_host for existing_host in existing_hosts_in_db if existing_host.name not in cleaned_host_names
        ]
        # Delete hosts and their associated HostIP instances
        for host_to_delete in hosts_to_delete_from_db:
            # Delete associated HostIP instances
            HostIP.objects.filter(host=host_to_delete).delete()
            # Delete the host itself
            host_to_delete.delete()
        # Update or create Hosts and HostIPs
        for cleaned_host in cleaned_hosts:
            # Check if the cleaned_host already exists
            host_in_db, host_created = Host.objects.get_or_create(domain=self, name=cleaned_host["name"])
            # Check if the nameserver is a subdomain of the current domain
            # If it is NOT a subdomain, we remove the IP address
            if not Domain.isSubdomain(self.name, cleaned_host["name"]):
                cleaned_host["addrs"] = []
            # Get cleaned list of ips for update
            cleaned_ips = cleaned_host["addrs"]
            if not host_created:
                # Get all existing ips from the database for this host
                existing_ips_in_db = HostIP.objects.filter(host=host_in_db)
                # Identify IPs to delete
                ips_to_delete_from_db = [
                    existing_ip for existing_ip in existing_ips_in_db if existing_ip.address not in cleaned_ips
                ]
                # Delete IPs
                for ip_to_delete in ips_to_delete_from_db:
                    # Delete the ip
                    ip_to_delete.delete()
            # Update or create HostIP instances
            for ip_address in cleaned_ips:
                HostIP.objects.get_or_create(address=ip_address, host=host_in_db)

    def _update_security_contact_in_db(self, cleaned):
        """Update security contact registry id in database if retrieved from registry.
        If no value is retrieved from registry, set to empty string in db.

        Parameters:
            self: the domain to be updated with security from cleaned
            cleaned: dict containing contact registry ids. Security contact is of type
                PublicContact.ContactTypeChoices.SECURITY
        """
        cleaned_contacts = cleaned["contacts"]
        security_contact_registry_id = ""
        security_contact = cleaned_contacts[PublicContact.ContactTypeChoices.SECURITY]
        if security_contact:
            security_contact_registry_id = security_contact
        self.security_contact_registry_id = security_contact_registry_id
        self.save()

    def _update_dates(self, cleaned):
        """Update dates (expiration and creation) from cleaned"""
        requires_save = False

        # if expiration date from registry does not match what is in db,
        # update the db
        if "ex_date" in cleaned and cleaned["ex_date"] != self.expiration_date:
            self.expiration_date = cleaned["ex_date"]
            requires_save = True

        # if creation_date from registry does not match what is in db,
        # update the db
        if "cr_date" in cleaned and cleaned["cr_date"] != self.created_at:
            self.created_at = cleaned["cr_date"]
            requires_save = True

        # if either registration date or creation date need updating
        if requires_save:
            self.save()

    def _get_contacts(self, contacts):
        choices = PublicContact.ContactTypeChoices
        # We expect that all these fields get populated,
        # so we can create these early, rather than waiting.
        cleaned_contacts = {
            choices.ADMINISTRATIVE: None,
            choices.SECURITY: None,
            choices.TECHNICAL: None,
        }
        if contacts and isinstance(contacts, list) and len(contacts) > 0:
            cleaned_contacts = self._fetch_contacts(contacts)
        return cleaned_contacts

    def _get_hosts(self, hosts):
        cleaned_hosts = []
        if hosts and isinstance(hosts, list):
            cleaned_hosts = self._fetch_hosts(hosts)
        return cleaned_hosts

    def _get_or_create_public_contact(self, public_contact: PublicContact):
        """Tries to find a PublicContact object in our DB.
        If it can't, it'll create it. Returns PublicContact"""
        db_contact = PublicContact.objects.filter(
            registry_id=public_contact.registry_id,
            contact_type=public_contact.contact_type,
            domain=self,
        )

        # If we find duplicates, log it and delete the oldest ones.
        if db_contact.count() > 1:
            logger.warning("_get_or_create_public_contact() -> Duplicate contacts found. Deleting duplicate.")

            newest_duplicate = db_contact.order_by("-created_at").first()

            duplicates_to_delete = db_contact.exclude(id=newest_duplicate.id)  # type: ignore

            # Delete all duplicates
            duplicates_to_delete.delete()

            # Do a second filter to grab the latest content
            db_contact = PublicContact.objects.filter(
                registry_id=public_contact.registry_id,
                contact_type=public_contact.contact_type,
                domain=self,
            )

        # Save to DB if it doesn't exist already.
        if db_contact.count() == 0:
            # Doesn't run custom save logic, just saves to DB
            try:
                with transaction.atomic():
                    public_contact.save(skip_epp_save=True)
                    logger.info(f"Created a new PublicContact: {public_contact}")
            except IntegrityError as err:
                logger.error(
                    f"_get_or_create_public_contact() => tried to create a duplicate public contact: {err}",
                    exc_info=True,
                )
                return PublicContact.objects.get(
                    registry_id=public_contact.registry_id,
                    contact_type=public_contact.contact_type,
                    domain=self,
                )

            # Append the item we just created
            return public_contact

        existing_contact = db_contact.get()

        # Does the item we're grabbing match what we have in our DB?
        if existing_contact.email != public_contact.email or existing_contact.registry_id != public_contact.registry_id:
            existing_contact.delete()
            public_contact.save()
            logger.warning("Requested PublicContact is out of sync with DB.")
            return public_contact

        # If it already exists, we can assume that the DB instance was updated during set, so we should just use that.
        return existing_contact

    def _registrant_to_public_contact(self, registry_id: str):
        """EPPLib returns the registrant as a string,
        which is the registrants associated registry_id. This function is used to
        convert that id to a useable object by calling commands.InfoContact
        on that ID, then mapping that object to type PublicContact."""
        contact = PublicContact(
            registry_id=registry_id,
            contact_type=PublicContact.ContactTypeChoices.REGISTRANT,
        )
        # Grabs the expanded contact
        full_object = self._request_contact_info(contact)
        # Maps it to type PublicContact
        mapped_object = self.map_epp_contact_to_public_contact(full_object, contact.registry_id, contact.contact_type)
        return self._get_or_create_public_contact(mapped_object)

    def _invalidate_cache(self):
        """Remove cache data when updates are made."""
        self._cache = {}

    def _get_property(self, property):
        """Get some piece of info about a domain."""
        if property not in self._cache:
            self._fetch_cache(
                fetch_hosts=(property == "hosts"),
                fetch_contacts=(property == "contacts"),
            )

        if property in self._cache:
            return self._cache[property]
        else:
            raise KeyError("Requested key %s was not found in registry cache." % str(property))
