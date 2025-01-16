# Cloning Databases
The clone-db workflow clones a Source database to a Destination database using cloud.gov's cg-manage-rds tool. This document contains additional information needed to understand how the workflow functions.

## Additional Roles Required
The clone-db workflow functions by temporarily sharing the Destination database with the space of the Source database. This is because cloning databases across spaces is hard. Sharing is done via the `cf share-service` command, but requires that the authenticated user (in this case this will be a user from the Source space) have the `space-developer` role in *both* the Source and Destination spaces. This must be set by someone with permission to edit space roles *before* the workflow runs. The user in question can be found using the `cf space-users [ORG] [SPACE]` command where the SPACE is the Source space, and will appear as a UAA user with a UUID as the name. There is only one such user per space by default (this is a [service account](https://cloud.gov/docs/services/cloud-gov-service-account/) set up by cloud.gov for our Github workflows). This user needs to be provided with the `space-developer` role in the Destination space, which can be accomplished using `cf set-space-role [USER] [ORG] [DESTINATION SPACE] SpaceDeveloper`.

## Turning Off DB Cloning Fast (For Emergencies or other Scenarios)
Note: In less urgent situations it may be better to make a PR removing the scheduled workflow trigger.

Step 1:
Get the name of the correct service using `cf spaces-users cisa-dotgov stable`. There should only be one user with a name that is a UUID, that is the one you want.

step 2:
Remove the space developer role by doing the following command:
`cf unset-space-role [USER] cisa-dotgov staging SpaceDeveloper`

This will cause the job to fail without requiring pushing anything to main.
