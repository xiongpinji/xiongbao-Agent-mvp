# Objective
Implement all approved C1 field/catalog controls and actual Plan/Detail API wiring, preserving B2 content and drafts/account privacy.

# Why
Today editor fields status/assignee only, list409closesdraft and detail identity ignoresaccount. Shared fields must persist real dates/priority/tags for future fiveviews.

# Scope
Own dashboard/src/api/modules/projectTodos.ts/test and newprojectTodoCatalog.ts/test; pages/Projects/ProjectPlan.tsx/test/module.less, ProjectTodoDetail.tsx/test/module.less, ProjectDetail.tsx/test (only accountloadidentity); new TodoFields.tsx/test, TodoCatalogManager.tsx/test, planDates.ts/test and scopedstyles/hooks; locales zh/en projectfield/catalog sections; existingprojecttestfixtures needing five mandatoryDTOfields.
Do not edit request.ts/tests (Codex R1), backend, planviewsAPI/components (C2 later), 030 untrackedtest. You are not alone; preserve others'changes, no reverting.

# Files to inspect
AGENTS; approvedC1; projectTodos typedAPI, ProjectPlan TodoEditorModal/submitEditor/routeMutationError/ACL/fetchSeq/board; ProjectTodoDetail Props/key/clearPrivateState/saveField/B2images; ProjectDetail useProjectLoader; useCurrentUser.tsx; request API signal (no edits); useServerTimezone/formatMessageTime only auditinstants; existingthreepage Vitestfixtures.

# Implementation guidance
TDD planDates and newcontrols then existingpagecases. Add mandatory DTO fivefields and create/updatebody exact C1 (null clear/omitted preserve/tagidsfullarray/catalogrevision). Testsfixtures add null/null/null/[]/1, don't loosenDTO to optional.
CatalogAPI real9writeendpoints+GET; no fakefallback metadata. Shared controls daystrings/calendararithmetic, server_today/timezone, explicitloadfailure retry/date disabled; fourdefaultsfromserver, search/none/multitag/colorsoftarchive/historyretain/remove/managerCRUDorder and409draftcompare. Catalogmanagement returntooriginalfielddraft and allowselectnewsuccessoption.
Create has actualstatus, date/priority/tags; tableboarddisplay fullprops and sameid; Detail rightproperties reusablefields/ACL, preserveplain/Markdown/comments/images.
Allreads/mutations captureaccount+project+todoidentity, addcurrentUserId toDetailkey, ProjectDetailloadkeyaccount. Latebothsuccess/error ignored inclcallbacks. 403 retainreadcontent/drafts; project/todo404 clearsprivatecatalog/fields/comments/images; option422notrevoke.
Plan409 keepsmodal+draft and refreshedservercomparison, no autoretry/close. Date422crossmidnight refreshmetadata retainsinput. Esc/focus/keyboardscroll1280/800, Chinese/Englishmatching. Ordinary flow do not showrevision/cursor/providerengineeringdetails.

# Constraints
No commits/push/release/deploy/credentials/npm install/dependency change/nestedagents/reset/clean/unrelatedformat. Onlynewcheckout. Node modulesjunction preinstalled; nopaidmodels/realprojects. Backend M2 inparallel usesexactapprovedspec.

# Acceptance criteria
Newcontrols and full oldpage tests validRED→GREEN, tsc; actualC1API calls fielddraft/null/version contract. Dto response fromcommittedserver istruth. Three-role/narrowscreen/TCP-PG browser laterindependentlyverified, not claimed fromVitest.

# Validation
cwd dashboard:
npm.cmd run test -- src/api/modules/projectTodos.test.ts src/api/modules/projectTodoCatalog.test.ts src/pages/Projects/planDates.test.ts src/pages/Projects/TodoFields.test.tsx src/pages/Projects/TodoCatalogManager.test.tsx src/pages/Projects/ProjectPlan.test.tsx src/pages/Projects/ProjectTodoDetail.test.tsx src/pages/Projects/ProjectDetail.test.tsx src/pages/Projects/ProjectTodoMarkdown.test.tsx
npx.cmd tsc -b
npx.cmd prettier --check src/api/modules/projectTodos.ts src/api/modules/projectTodos.test.ts src/api/modules/projectTodoCatalog.ts src/api/modules/projectTodoCatalog.test.ts src/pages/Projects/ProjectPlan.tsx src/pages/Projects/ProjectPlan.test.tsx src/pages/Projects/ProjectPlan.module.less src/pages/Projects/ProjectTodoDetail.tsx src/pages/Projects/ProjectTodoDetail.test.tsx src/pages/Projects/ProjectTodoDetail.module.less src/pages/Projects/ProjectDetail.tsx src/pages/Projects/ProjectDetail.test.tsx src/pages/Projects/TodoFields.tsx src/pages/Projects/TodoFields.test.tsx src/pages/Projects/TodoCatalogManager.tsx src/pages/Projects/TodoCatalogManager.test.tsx src/pages/Projects/planDates.ts src/pages/Projects/planDates.test.ts src/locales/zh.json src/locales/en.json
Format only these files if needed.
No full build totracked src/octop/dashboard; Codexrunsisolatedbuild later.

# Final report
DONE or NEEDS_CONTEXT exactfiles, contracts/props, RED/GREEN commands/casecounts/exits, anylimitations/QA remaining. No commits. Await independent reviews; don'tspawn anotheragent.
