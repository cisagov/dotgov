import { hookupYesNoListener, hookupRadioTogglerListener } from './radios.js';
import { initDomainValidators } from './domain-validators.js';
import { initFormsetsForms, triggerModalOnDsDataForm, nameserversFormListener } from './formset-forms.js';
import { initializeUrbanizationToggle } from './urbanization.js';
import { userProfileListener, finishUserSetupListener } from './user-profile.js';
import { loadInitialValuesForComboBoxes } from './combobox.js';
import { handleRequestingEntityFieldset } from './requesting-entity.js';
import { initDomainsTable } from './table-domains.js';
import { initDomainRequestsTable } from './table-domain-requests.js';
import { initMembersTable } from './table-members.js';
import { initMemberDomainsTable } from './table-member-domains.js';
import { initPortfolioNewMemberPageToggle, initAddNewMemberPageListeners, initPortfolioMemberPage } from './portfolio-member-page.js';

initDomainValidators();

initFormsetsForms();
triggerModalOnDsDataForm();
nameserversFormListener();

hookupYesNoListener("other_contacts-has_other_contacts",'other-employees', 'no-other-employees');
hookupYesNoListener("additional_details-has_anything_else_text",'anything-else', null);
hookupYesNoListener("additional_details-has_cisa_representative",'cisa-representative', null);
initializeUrbanizationToggle();

userProfileListener();
finishUserSetupListener();

loadInitialValuesForComboBoxes();

handleRequestingEntityFieldset();

initDomainsTable();
initDomainRequestsTable();
initMembersTable();
initMemberDomainsTable();

// Init the portfolio new member page
initPortfolioNewMemberPageToggle();
initAddNewMemberPageListeners();

// Init all other portfolio member pages
initPortfolioMemberPage();