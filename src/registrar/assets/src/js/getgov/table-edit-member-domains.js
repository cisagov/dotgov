
import { BaseTable } from './table-base.js';

export class EditMemberDomainsTable extends BaseTable {

  constructor() {
    super('edit-member-domain');
    this.currentSortBy = 'name';
  }
  getBaseUrl() {
    return document.getElementById("get_member_domains_edit_json_url");
  }
  getDataObjects(data) {
    return data.domains;
  }
  addRow(dataObject, tbody, customTableOptions) {
    const domain = dataObject;
    const row = document.createElement('tr');
    row.innerHTML = `
        <td data-label="Selected" data-sort-value="0">
            <div class="usa-checkbox">
                <input
                    class="usa-checkbox__input"
                    id="${domain.id}"
                    type="checkbox"
                    name="${domain.name}"
                    value="${domain.id}"
                />
                <label class="usa-checkbox__label" for="${domain.id}">
                    <span class="sr-only">${domain.id}</span>
                </label>
            </div>
        </td>
        <td data-label="Domain name">
            ${domain.name}
        </td>
    `;
    tbody.appendChild(row);
  }

}
  
export function initEditMemberDomainsTable() {
  document.addEventListener('DOMContentLoaded', function() {
      const isEditMemberDomainsPage = document.getElementById("edit-member-domains");
      if (isEditMemberDomainsPage){
        const editMemberDomainsTable = new EditMemberDomainsTable();
        if (editMemberDomainsTable.tableWrapper) {
          // Initial load
          editMemberDomainsTable.loadTable(1);
        }
      }
    });
}
