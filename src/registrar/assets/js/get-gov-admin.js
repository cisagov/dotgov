/**
 * @file get-gov-admin.js includes custom code for the .gov registrar admin portal.
 *
 * Constants and helper functions are at the top.
 * Event handlers are in the middle.
 * Initialization (run-on-load) stuff goes at the bottom.
 */

// <<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>>
// Helper functions.

/**
 * Hide element
 *
*/
const hideElement = (element) => {
    if (element && !element.classList.contains("display-none"))
        element.classList.add('display-none');
};

/**
 * Show element
 *
 */
const showElement = (element) => {
    if (element && element.classList.contains("display-none"))
        element.classList.remove('display-none');
};

/** Either sets attribute target="_blank" to a given element, or removes it */
function openInNewTab(el, removeAttribute = false){
    if(removeAttribute){
        el.setAttribute("target", "_blank");
    }else{
        el.removeAttribute("target", "_blank");
    }
};

// Adds or removes a boolean from our session
function addOrRemoveSessionBoolean(name, add){
    if (add) {
        sessionStorage.setItem(name, "true");
    }else {
        sessionStorage.removeItem(name); 
    }
}

// <<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>>
// Event handlers.

// <<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>><<>>
// Initialization code.

/** An IIFE for pages in DjangoAdmin that use modals.
 * Dja strips out form elements, and modals generate their content outside
 * of the current form scope, so we need to "inject" these inputs.
*/
(function (){
    function createPhantomModalFormButtons(){
        let submitButtons = document.querySelectorAll('.usa-modal button[type="submit"].dja-form-placeholder');
        form = document.querySelector("form")
        submitButtons.forEach((button) => {

            let input = document.createElement("input");
            input.type = "submit";

            if(button.name){
                input.name = button.name;
            }

            if(button.value){
                input.value = button.value;
            }

            input.style.display = "none"

            // Add the hidden input to the form
            form.appendChild(input);
            button.addEventListener("click", () => {
                input.click();
            })
        })
    }

    createPhantomModalFormButtons();
})();


/** An IIFE for DomainRequest to hook a modal to a dropdown option.
 * This intentionally does not interact with createPhantomModalFormButtons()
*/
(function (){
    function displayModalOnDropdownClick(linkClickedDisplaysModal, statusDropdown, actionButton, valueToCheck){

        // If these exist all at the same time, we're on the right page
        if (linkClickedDisplaysModal && statusDropdown && statusDropdown.value){
            
            // Set the previous value in the event the user cancels.
            let previousValue = statusDropdown.value;
            if (actionButton){

                // Otherwise, if the confirmation buttion is pressed, set it to that
                actionButton.addEventListener('click', function() {
                    // Revert the dropdown to its previous value
                    statusDropdown.value = valueToCheck;
                });
            }else {
                console.log("displayModalOnDropdownClick() -> Cancel button was null")
            }

            // Add a change event listener to the dropdown.
            statusDropdown.addEventListener('change', function() {
                // Check if "Ineligible" is selected
                if (this.value && this.value.toLowerCase() === valueToCheck) {
                    // Set the old value in the event the user cancels,
                    // or otherwise exists the dropdown.
                    statusDropdown.value = previousValue

                    // Display the modal.
                    linkClickedDisplaysModal.click()
                }
            });
        }
    }

    // When the status dropdown is clicked and is set to "ineligible", toggle a confirmation dropdown.
    function hookModalToIneligibleStatus(){
        // Grab the invisible element that will hook to the modal.
        // This doesn't technically need to be done with one, but this is simpler to manage.
        let modalButton = document.getElementById("invisible-ineligible-modal-toggler")
        let statusDropdown = document.getElementById("id_status")

        // Because the modal button does not have the class "dja-form-placeholder",
        // it will not be affected by the createPhantomModalFormButtons() function.
        let actionButton = document.querySelector('button[name="_set_domain_request_ineligible"]');
        let valueToCheck = "ineligible"
        displayModalOnDropdownClick(modalButton, statusDropdown, actionButton, valueToCheck);
    }

    hookModalToIneligibleStatus()
})();

/** An IIFE for pages in DjangoAdmin which may need custom JS implementation.
 * Currently only appends target="_blank" to the domain_form object,
 * but this can be expanded.
*/
(function (){
    /*
    On mouseover, appends target="_blank" on domain_form under the Domain page.
    The reason for this is that the template has a form that contains multiple buttons.
    The structure of that template complicates seperating those buttons 
    out of the form (while maintaining the same position on the page).
    However, if we want to open one of those submit actions to a new tab - 
    such as the manage domain button - we need to dynamically append target.
    As there is no built-in django method which handles this, we do it here. 
    */
    function prepareDjangoAdmin() {
        let domainFormElement = document.getElementById("domain_form");
        let domainSubmitButton = document.getElementById("manageDomainSubmitButton");
        if(domainSubmitButton && domainFormElement){
            domainSubmitButton.addEventListener("mouseover", () => openInNewTab(domainFormElement, true));
            domainSubmitButton.addEventListener("mouseout", () => openInNewTab(domainFormElement, false));
        }
    }

    prepareDjangoAdmin();
})();


/** An IIFE for the "Assign to me" button under the investigator field in DomainRequests.
** This field uses the "select2" selector, rather than the default. 
** To perform data operations on this - we need to use jQuery rather than vanilla js. 
*/
(function (){
    let selector = django.jQuery("#id_investigator")
    let assignSelfButton = document.querySelector("#investigator__assign_self");
    if (!selector || !assignSelfButton) {
        return;
    }

    let currentUserId = assignSelfButton.getAttribute("data-user-id");
    let currentUserName = assignSelfButton.getAttribute("data-user-name");
    if (!currentUserId || !currentUserName){
        console.error("Could not assign current user: no values found.")
        return;
    }

    // Hook a click listener to the "Assign to me" button.
    // Logic borrowed from here: https://select2.org/programmatic-control/add-select-clear-items#create-if-not-exists
    assignSelfButton.addEventListener("click", function() {
        if (selector.find(`option[value='${currentUserId}']`).length) {
            // Select the value that is associated with the current user.
            selector.val(currentUserId).trigger("change");
        } else { 
            // Create a DOM Option that matches the desired user. Then append it and select it.
            let userOption = new Option(currentUserName, currentUserId, true, true);
            selector.append(userOption).trigger("change");
        }
    });

    // Listen to any change events, and hide the parent container if investigator has a value.
    selector.on('change', function() {
        // The parent container has display type flex.
        assignSelfButton.parentElement.style.display = this.value === currentUserId ? "none" : "flex";
    });
    
    

})();

/** An IIFE for pages in DjangoAdmin that use a clipboard button
*/
(function (){

    function copyToClipboardAndChangeIcon(button) {
        // Assuming the input is the previous sibling of the button
        let input = button.previousElementSibling;
        let userId = input.getAttribute("user-id")
        // Copy input value to clipboard
        if (input) {
            navigator.clipboard.writeText(input.value).then(function() {
                // Change the icon to a checkmark on successful copy
                let buttonIcon = button.querySelector('.copy-to-clipboard use');
                if (buttonIcon) {
                    let currentHref = buttonIcon.getAttribute('xlink:href');
                    let baseHref = currentHref.split('#')[0];

                    // Append the new icon reference
                    buttonIcon.setAttribute('xlink:href', baseHref + '#check');

                    // Change the button text
                    let nearestSpan = button.querySelector("span")
                    let original_text = nearestSpan.innerText
                    nearestSpan.innerText = "Copied to clipboard"

                    setTimeout(function() {
                        // Change back to the copy icon
                        buttonIcon.setAttribute('xlink:href', currentHref); 
                        nearestSpan.innerText = original_text;
                    }, 2000);

                }
            }).catch(function(error) {
                console.error('Clipboard copy failed', error);
            });
        }
    }
    
    function handleClipboardButtons() {
        clipboardButtons = document.querySelectorAll(".copy-to-clipboard")
        clipboardButtons.forEach((button) => {

            // Handle copying the text to your clipboard,
            // and changing the icon.
            button.addEventListener("click", ()=>{
                copyToClipboardAndChangeIcon(button);
            });
            
            // Add a class that adds the outline style on click
            button.addEventListener("mousedown", function() {
                this.classList.add("no-outline-on-click");
            });
            
            // But add it back in after the user clicked,
            // for accessibility reasons (so we can still tab, etc)
            button.addEventListener("blur", function() {
                this.classList.remove("no-outline-on-click");
            });

        });
    }

    handleClipboardButtons();
})();


/**
 * An IIFE to listen to changes on filter_horizontal and enable or disable the change/delete/view buttons as applicable
 *
 */
(function extendFilterHorizontalWidgets() {
    // Initialize custom filter_horizontal widgets; each widget has a "from" select list
    // and a "to" select list; initialization is based off of the presence of the
    // "to" select list
    checkToListThenInitWidget('id_groups_to', 0);
    checkToListThenInitWidget('id_user_permissions_to', 0);
    checkToListThenInitWidget('id_portfolio_roles_to', 0);
    checkToListThenInitWidget('id_portfolio_additional_permissions_to', 0);
})();

// Function to check for the existence of the "to" select list element in the DOM, and if and when found,
// initialize the associated widget
function checkToListThenInitWidget(toListId, attempts) {
    let toList = document.getElementById(toListId);
    attempts++;

    if (attempts < 12) {
        if (toList) {
            // toList found, handle it
            // Then get fromList and handle it
            initializeWidgetOnList(toList, ".selector-chosen");
            let fromList = toList.closest('.selector').querySelector(".selector-available select");
            initializeWidgetOnList(fromList, ".selector-available");
        } else {
            // Element not found, check again after a delay
            setTimeout(() => checkToListThenInitWidget(toListId, attempts), 300); // Check every 300 milliseconds
        }
    }
}

// Initialize the widget:
// Replace h2 with more semantic h3
function initializeWidgetOnList(list, parentId) {    
    if (list) {
        // Get h2 and its container
        const parentElement = list.closest(parentId);
        const h2Element = parentElement.querySelector('h2');

        // One last check
        if (parentElement && h2Element) {
            // Create a new <h3> element
            const h3Element = document.createElement('h3');

            // Copy the text content from the <h2> element to the <h3> element
            h3Element.textContent = h2Element.textContent;

            // Find the nested <span> element inside the <h2>
            const nestedSpan = h2Element.querySelector('span[class][title]');

            // If the nested <span> element exists
            if (nestedSpan) {
                // Create a new <span> element
                const newSpan = document.createElement('span');

                // Copy the class and title attributes from the nested <span> element
                newSpan.className = nestedSpan.className;
                newSpan.title = nestedSpan.title;

                // Append the new <span> element to the <h3> element
                h3Element.appendChild(newSpan);
            }

            // Replace the <h2> element with the new <h3> element
            parentElement.replaceChild(h3Element, h2Element);
        }
    }
}

/** An IIFE for admin in DjangoAdmin to listen to changes on the domain request
 * status select and to show/hide the rejection reason
*/
(function (){
    let rejectionReasonFormGroup = document.querySelector('.field-rejection_reason')
    // This is the "action needed reason" field
    let actionNeededReasonFormGroup = document.querySelector('.field-action_needed_reason');
    // This is the "auto-generated email" field
    let actionNeededReasonEmailFormGroup = document.querySelector('.field-action_needed_reason_email')

    if (rejectionReasonFormGroup && actionNeededReasonFormGroup && actionNeededReasonEmailFormGroup) {
        let statusSelect = document.getElementById('id_status')
        let isRejected = statusSelect.value == "rejected"
        let isActionNeeded = statusSelect.value == "action needed"

        // Initial handling of rejectionReasonFormGroup display
        showOrHideObject(rejectionReasonFormGroup, show=isRejected)
        showOrHideObject(actionNeededReasonFormGroup, show=isActionNeeded)
        showOrHideObject(actionNeededReasonEmailFormGroup, show=isActionNeeded)

        // Listen to change events and handle rejectionReasonFormGroup display, then save status to session storage
        statusSelect.addEventListener('change', function() {
            // Show the rejection reason field if the status is rejected.
            // Then track if its shown or hidden in our session cache.
            isRejected = statusSelect.value == "rejected"
            showOrHideObject(rejectionReasonFormGroup, show=isRejected)
            addOrRemoveSessionBoolean("showRejectionReason", add=isRejected)

            isActionNeeded = statusSelect.value == "action needed"
            showOrHideObject(actionNeededReasonFormGroup, show=isActionNeeded)
            showOrHideObject(actionNeededReasonEmailFormGroup, show=isActionNeeded)
            addOrRemoveSessionBoolean("showActionNeededReason", add=isActionNeeded)
        });
        
        // Listen to Back/Forward button navigation and handle rejectionReasonFormGroup display based on session storage

        // When you navigate using forward/back after changing status but not saving, when you land back on the DA page the
        // status select will say (for example) Rejected but the selected option can be something else. To manage the show/hide
        // accurately for this edge case, we use cache and test for the back/forward navigation.
        const observer = new PerformanceObserver((list) => {
            list.getEntries().forEach((entry) => {
            if (entry.type === "back_forward") {
                let showRejectionReason = sessionStorage.getItem("showRejectionReason") !== null
                showOrHideObject(rejectionReasonFormGroup, show=showRejectionReason)

                let showActionNeededReason = sessionStorage.getItem("showActionNeededReason") !== null
                showOrHideObject(actionNeededReasonFormGroup, show=showActionNeededReason)
                showOrHideObject(actionNeededReasonEmailFormGroup, show=isActionNeeded)
            }
            });
        });
        observer.observe({ type: "navigation" });
    }

    // Adds or removes the display-none class to object depending on the value of boolean show
    function showOrHideObject(object, show){
        if (show){
            object.classList.remove("display-none");
        }else {
            object.classList.add("display-none");
        }
    }
})();

/** An IIFE for toggling the submit bar on domain request forms
*/
(function (){
    // Get a reference to the button element
    const toggleButton = document.getElementById('submitRowToggle');
    const submitRowWrapper = document.querySelector('.submit-row-wrapper');

    if (toggleButton) {
        // Add event listener to toggle the class and update content on click
        toggleButton.addEventListener('click', function() {
            // Toggle the 'collapsed' class on the bar
            submitRowWrapper.classList.toggle('submit-row-wrapper--collapsed');

            // Get a reference to the span element inside the button
            const spanElement = this.querySelector('span');

            // Get a reference to the use element inside the button
            const useElement = this.querySelector('use');

            // Check if the span element text is 'Hide'
            if (spanElement.textContent.trim() === 'Hide') {
                // Update the span element text to 'Show'
                spanElement.textContent = 'Show';

                // Update the xlink:href attribute to expand_more
                useElement.setAttribute('xlink:href', '/public/img/sprite.svg#expand_less');
            } else {
                // Update the span element text to 'Hide'
                spanElement.textContent = 'Hide';

                // Update the xlink:href attribute to expand_less
                useElement.setAttribute('xlink:href', '/public/img/sprite.svg#expand_more');
            }
        });

        // We have a scroll indicator at the end of the page.
        // Observe it. Once it gets on screen, test to see if the row is collapsed.
        // If it is, expand it.
        const targetElement = document.querySelector(".scroll-indicator");
        const options = {
            threshold: 1
        };
        // Create a new Intersection Observer
        const observer = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    // Refresh reference to submit row wrapper and check if it's collapsed
                    if (document.querySelector('.submit-row-wrapper').classList.contains('submit-row-wrapper--collapsed')) {
                        toggleButton.click();
                    }
                }
            });
        }, options);
        observer.observe(targetElement);
    }
})();

/** An IIFE for toggling the overflow styles on django-admin__model-description (the show more / show less button) */
(function () {
    function handleShowMoreButton(toggleButton, descriptionDiv){
        // Check the length of the text content in the description div
        if (descriptionDiv.textContent.length < 200) {
            // Hide the toggle button if text content is less than 200 characters
            // This is a little over 160 characters to give us some wiggle room if we
            // change the font size marginally.
            toggleButton.classList.add('display-none');
        } else {
            toggleButton.addEventListener('click', function() {
                toggleShowMoreButton(toggleButton, descriptionDiv, 'dja__model-description--no-overflow')
            });
        }
    }

    function toggleShowMoreButton(toggleButton, descriptionDiv, showMoreClassName){
        // Toggle the class on the description div
        descriptionDiv.classList.toggle(showMoreClassName);

        // Change the button text based on the presence of the class
        if (descriptionDiv.classList.contains(showMoreClassName)) {
            toggleButton.textContent = 'Show less';
        } else {    
            toggleButton.textContent = 'Show more';
        }
    }

    let toggleButton = document.getElementById('dja-show-more-model-description');
    let descriptionDiv = document.querySelector('.dja__model-description');
    if (toggleButton && descriptionDiv) {
        handleShowMoreButton(toggleButton, descriptionDiv)
    }
})();


/** An IIFE that hooks to the show/hide button underneath action needed reason.
 * This shows the auto generated email on action needed reason.
*/
(function () {
    // Since this is an iife, these vars will be removed from memory afterwards
    var actionNeededReasonDropdown = document.querySelector("#id_action_needed_reason");
    var actionNeededEmail = document.querySelector("#id_action_needed_reason_email");
    var readonlyView = document.querySelector("#action-needed-reason-email-readonly");

    let emailWasSent = document.getElementById("action-needed-email-sent");
    let actionNeededEmailData = document.getElementById('action-needed-emails-data').textContent;
    let actionNeededEmailsJson = JSON.parse(actionNeededEmailData);

    const domainRequestId = actionNeededReasonDropdown ? document.querySelector("#domain_request_id").value : null
    const emailSentSessionVariableName = `actionNeededEmailSent-${domainRequestId}`;
    const oldDropdownValue = actionNeededReasonDropdown ? actionNeededReasonDropdown.value : null;
    const oldEmailValue = actionNeededEmailData ? actionNeededEmailData.value : null;

    if(actionNeededReasonDropdown && actionNeededEmail && domainRequestId) {
        // Add a change listener to dom load
        document.addEventListener('DOMContentLoaded', function() {
            let reason = actionNeededReasonDropdown.value;

            // Handle the session boolean (to enable/disable editing)
            if (emailWasSent && emailWasSent.value === "True") {
                // An email was sent out - store that information in a session variable
                addOrRemoveSessionBoolean(emailSentSessionVariableName, add=true);
            }

            // Show an editable email field or a readonly one
            updateActionNeededEmailDisplay(reason)
        });

        // Add a change listener to the action needed reason dropdown
        actionNeededReasonDropdown.addEventListener("change", function() {
            let reason = actionNeededReasonDropdown.value;
            let emailBody = reason in actionNeededEmailsJson ? actionNeededEmailsJson[reason] : null;
            if (reason && emailBody) {
                // Replace the email content
                actionNeededEmail.value = emailBody;

                // Reset the session object on change since change refreshes the email content.
                if (oldDropdownValue !== actionNeededReasonDropdown.value || oldEmailValue !== actionNeededEmail.value) {
                    let emailSent = sessionStorage.getItem(emailSentSessionVariableName)
                    if (emailSent !== null){
                        addOrRemoveSessionBoolean(emailSentSessionVariableName, add=false)
                    }
                }
            }

            // Show an editable email field or a readonly one
            updateActionNeededEmailDisplay(reason)
        });
    }

    // Shows an editable email field or a readonly one.
    // If the email doesn't exist or if we're of reason "other", display that no email was sent.
    // Likewise, if we've sent this email before, we should just display the content.
    function updateActionNeededEmailDisplay(reason) {
        let emailHasBeenSentBefore = sessionStorage.getItem(emailSentSessionVariableName) !== null;
        let collapseableDiv = readonlyView.querySelector(".collapse--dgsimple");
        let showMoreButton = document.querySelector("#action_needed_reason_email__show_details");
        if ((reason && reason != "other") && !emailHasBeenSentBefore) {
            showElement(actionNeededEmail.parentElement)
            hideElement(readonlyView)
            hideElement(showMoreButton)
        } else {
            if (!reason || reason === "other") {
                collapseableDiv.innerHTML = reason ? "No email will be sent." : "-";
                hideElement(showMoreButton)
                if (collapseableDiv.classList.contains("collapsed")) {
                    showMoreButton.click()
                }
            }else {
                showElement(showMoreButton)
            }
            hideElement(actionNeededEmail.parentElement)
            showElement(readonlyView)
        }
    }
})();


/** An IIFE for copy summary button (appears in DomainRegistry models)
*/
(function (){
    const copyButton = document.getElementById('id-copy-to-clipboard-summary');

    if (copyButton) {
        copyButton.addEventListener('click', function() {
            /// Generate a rich HTML summary text and copy to clipboard

            //------ Organization Type
            const organizationTypeElement = document.getElementById('id_organization_type');
            const organizationType = organizationTypeElement.options[organizationTypeElement.selectedIndex].text;

            //------ Alternative Domains
            const alternativeDomainsDiv = document.querySelector('.form-row.field-alternative_domains .readonly');
            const alternativeDomainslinks = alternativeDomainsDiv.querySelectorAll('a');
            const alternativeDomains = Array.from(alternativeDomainslinks).map(link => link.textContent);

            //------ Existing Websites
            const existingWebsitesDiv = document.querySelector('.form-row.field-current_websites .readonly');
            const existingWebsiteslinks = existingWebsitesDiv.querySelectorAll('a');
            const existingWebsites = Array.from(existingWebsiteslinks).map(link => link.textContent);

            //------ Additional Contacts
            // 1 - Create a hyperlinks map so we can display contact details and also link to the contact
            const otherContactsDiv = document.querySelector('.form-row.field-other_contacts .readonly');
            let otherContactLinks = [];
            const nameToUrlMap = {};
            if (otherContactsDiv) {
                otherContactLinks = otherContactsDiv.querySelectorAll('a');
                otherContactLinks.forEach(link => {
                const name = link.textContent.trim();
                const url = link.href;
                nameToUrlMap[name] = url;
                });
            }
        
            // 2 - Iterate through contact details and assemble html for summary
            let otherContactsSummary = ""
            const bulletList = document.createElement('ul');

            // CASE 1 - Contacts are not in a table (this happens if there is only one or two other contacts)
            const contacts = document.querySelectorAll('.field-other_contacts .dja-detail-list dd');
            if (contacts) {
                contacts.forEach(contact => {
                    // Check if the <dl> element is not empty
                    const name = contact.querySelector('a#contact_info_name')?.innerText;
                    const title = contact.querySelector('span#contact_info_title')?.innerText;
                    const email = contact.querySelector('span#contact_info_email')?.innerText;
                    const phone = contact.querySelector('span#contact_info_phone')?.innerText;
                    const url = nameToUrlMap[name] || '#';
                    // Format the contact information
                    const listItem = document.createElement('li');
                    listItem.innerHTML = `<a href="${url}">${name}</a>, ${title}, ${email}, ${phone}`;
                    bulletList.appendChild(listItem);
                });

            }

            // CASE 2 - Contacts are in a table (this happens if there is more than 2 contacts)
            const otherContactsTable = document.querySelector('.form-row.field-other_contacts table tbody');
            if (otherContactsTable) {
                const otherContactsRows = otherContactsTable.querySelectorAll('tr');
                otherContactsRows.forEach(contactRow => {
                // Extract the contact details
                const name = contactRow.querySelector('th').textContent.trim();
                const title = contactRow.querySelectorAll('td')[0].textContent.trim();
                const email = contactRow.querySelectorAll('td')[1].textContent.trim();
                const phone = contactRow.querySelectorAll('td')[2].textContent.trim();
                const url = nameToUrlMap[name] || '#';
                // Format the contact information
                const listItem = document.createElement('li');
                listItem.innerHTML = `<a href="${url}">${name}</a>, ${title}, ${email}, ${phone}`;
                bulletList.appendChild(listItem);
                });
            }
            otherContactsSummary += bulletList.outerHTML


            //------ Requested Domains
            const requestedDomainElement = document.getElementById('id_requested_domain');
            const requestedDomain = requestedDomainElement.options[requestedDomainElement.selectedIndex].text;

            //------ Submitter
            // Function to extract text by ID and handle missing elements
            function extractTextById(id, divElement) {
                if (divElement) {
                    const element = divElement.querySelector(`#${id}`);
                    return element ? ", " + element.textContent.trim() : '';
                }
                return '';
            }
            // Extract the submitter name, title, email, and phone number
            const submitterDiv = document.querySelector('.form-row.field-submitter');
            const submitterNameElement = document.getElementById('id_submitter');
            const submitterName = submitterNameElement.options[submitterNameElement.selectedIndex].text;
            const submitterTitle = extractTextById('contact_info_title', submitterDiv);
            const submitterEmail = extractTextById('contact_info_email', submitterDiv);
            const submitterPhone = extractTextById('contact_info_phone', submitterDiv);
            let submitterInfo = `${submitterName}${submitterTitle}${submitterEmail}${submitterPhone}`;


            //------ Senior Official
            const seniorOfficialDiv = document.querySelector('.form-row.field-senior_official');
            const seniorOfficialElement = document.getElementById('id_senior_official');
            const seniorOfficialName = seniorOfficialElement.options[seniorOfficialElement.selectedIndex].text;
            const seniorOfficialTitle = extractTextById('contact_info_title', seniorOfficialDiv);
            const seniorOfficialEmail = extractTextById('contact_info_email', seniorOfficialDiv);
            const seniorOfficialPhone = extractTextById('contact_info_phone', seniorOfficialDiv);
            let seniorOfficialInfo = `${seniorOfficialName}${seniorOfficialTitle}${seniorOfficialEmail}${seniorOfficialPhone}`;

            const html_summary = `<strong>Recommendation:</strong></br>` +
                            `<strong>Organization Type:</strong> ${organizationType}</br>` +
                            `<strong>Requested Domain:</strong> ${requestedDomain}</br>` +
                            `<strong>Current Websites:</strong> ${existingWebsites.join(', ')}</br>` +
                            `<strong>Rationale:</strong></br>` +
                            `<strong>Alternative Domains:</strong> ${alternativeDomains.join(', ')}</br>` +
                            `<strong>Submitter:</strong> ${submitterInfo}</br>` +
                            `<strong>Senior Official:</strong> ${seniorOfficialInfo}</br>` +
                            `<strong>Other Employees:</strong> ${otherContactsSummary}</br>`;
            
            //Replace </br> with \n, then strip out all remaining html tags (replace <...> with '')
            const plain_summary = html_summary.replace(/<\/br>|<br>/g, '\n').replace(/<\/?[^>]+(>|$)/g, '');

            // Create Blobs with the summary content
            const html_blob = new Blob([html_summary], { type: 'text/html' });
            const plain_blob = new Blob([plain_summary], { type: 'text/plain' });

            // Create a ClipboardItem with the Blobs
            const clipboardItem = new ClipboardItem({
                'text/html': html_blob,
                'text/plain': plain_blob
            });

            // Write the ClipboardItem to the clipboard
            navigator.clipboard.write([clipboardItem]).then(() => {
                // Change the icon to a checkmark on successful copy
                let buttonIcon = copyButton.querySelector('use');
                if (buttonIcon) {
                    let currentHref = buttonIcon.getAttribute('xlink:href');
                    let baseHref = currentHref.split('#')[0];

                    // Append the new icon reference
                    buttonIcon.setAttribute('xlink:href', baseHref + '#check');

                    // Change the button text
                    nearestSpan = copyButton.querySelector("span")
                    original_text = nearestSpan.innerText
                    nearestSpan.innerText = "Copied to clipboard"

                    setTimeout(function() {
                        // Change back to the copy icon
                        buttonIcon.setAttribute('xlink:href', currentHref); 
                        nearestSpan.innerText = original_text
                    }, 2000);

                }
                console.log('Summary copied to clipboard successfully!');
            }).catch(err => {
                console.error('Failed to copy text: ', err);
            });
        });
    }
})();
