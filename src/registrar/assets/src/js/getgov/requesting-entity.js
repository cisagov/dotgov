import { hideElement, showElement } from './helpers.js';

/** A function that intializes the requesting entity page.
 * This page has a radio button that dynamically toggles some fields
 * Within that, the dropdown also toggles some additional form elements.
*/
export function handleRequestingEntityFieldset() { 
    // Sadly, these ugly ids are the auto generated with this prefix
    const formPrefix = "portfolio_requesting_entity";
    const radioFieldset = document.getElementById(`id_${formPrefix}-requesting_entity_is_suborganization__fieldset`);
    const radios = radioFieldset?.querySelectorAll(`input[name="${formPrefix}-requesting_entity_is_suborganization"]`);
    const select = document.getElementById(`id_${formPrefix}-sub_organization`);
    const selectParent = select?.parentElement;
    const suborgContainer = document.getElementById("suborganization-container");
    const suborgDetailsContainer = document.getElementById("suborganization-container__details");
    const subOrgCreateNewOption = document.getElementById("option-to-add-suborg")?.value;
    // Make sure all crucial page elements exist before proceeding.
    // This more or less ensures that we are on the Requesting Entity page, and not elsewhere.
    if (!radios || !select || !selectParent || !suborgContainer || !suborgDetailsContainer) return;

    // requestingSuborganization: This just broadly determines if they're requesting a suborg at all
    // requestingNewSuborganization: This variable determines if the user is trying to *create* a new suborganization or not.
    var requestingSuborganization = Array.from(radios).find(radio => radio.checked)?.value === "True";
    var requestingNewSuborganization = document.getElementById(`id_${formPrefix}-is_requesting_new_suborganization`);

    function toggleSuborganization(radio=null) {
        if (radio != null) requestingSuborganization = radio?.checked && radio.value === "True";
        requestingSuborganization ? showElement(suborgContainer) : hideElement(suborgContainer);
        if (select.options.length == 2) { // --Select-- and other are the only options
            hideElement(selectParent); // Hide the select drop down and indicate requesting new suborg
            requestingNewSuborganization.value = "True";
        } else {
            requestingNewSuborganization.value = requestingSuborganization && select.value === "other" ? "True" : "False";
        }
        requestingNewSuborganization.value === "True" ? showElement(suborgDetailsContainer) : hideElement(suborgDetailsContainer);
    }

    // Add fake "other" option to sub_organization select
    if (select && !Array.from(select.options).some(option => option.value === "other")) {
        select.add(new Option(subOrgCreateNewOption, "other"));
    }

    if (requestingNewSuborganization.value === "True") {
        select.value = "other";
    }

    // Add event listener to is_suborganization radio buttons, and run for initial display
    toggleSuborganization();
    radios.forEach(radio => {
        radio.addEventListener("click", () => toggleSuborganization(radio));
    });

    // Add event listener to the suborg dropdown to show/hide the suborg details section
    select.addEventListener("change", () => toggleSuborganization());
}
