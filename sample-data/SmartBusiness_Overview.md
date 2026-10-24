# About SmartBusiness Solutions
SmartBusiness Solutions is a business platform with shared organization accounts and separate tools for day-to-day operations. It offers two main products: Check Printing, and Time and Attendance. Both are designed for organizations with one or more workspaces or locations. A company can give employees different access based on their role and responsibilities.

# Check Printing
Check Printing lets a business prepare, review, approve and print business checks. It also manages payees, bank accounts, print settings and check history.

# Time and Attendance
Time and Attendance lets employees record their work attendance and lets managers manage employee records, review attendance and view reports.

# Who uses the platform
- Organization owner or administrator: sets up the organization and workspaces, invites or manages members, assigns access and configures product settings.
- Finance team member: maintains payees and bank details, prepares checks and reviews check records. Permissions decide whether they can approve or print.
- Approver: reviews checks that need approval and approves or rejects them according to the organization's rules.
- Employee: uses the attendance experience to clock in or out and review their own attendance information.
- Attendance manager: manages employee and location details, reviews attendance records and exports reports.

Access is permission-based. For example, creating a check, approving it and printing it can be assigned to different people.

# How a user gets access
1. An organization is registered, or an authorized user signs in to an existing organization.
2. The system verifies the user's credentials and starts a secure session.
3. The user selects or enters the organization they are working in. The system checks their organization membership and whether the product is available to that organization.
4. For workspace-specific work, the system also checks that the user belongs to that workspace and has the required permission.
5. The user sees only the screens and actions allowed by their access level.

One shared account foundation means there are no unrelated logins for each product, and records stay scoped to the right business.

# Check Printing workflow
1. Set up the workspace: an administrator configures workspace details, check layout and printing preferences, and authorized bank accounts.
2. Set up payees: a permitted user adds or updates vendors and other recipients.
3. Prepare a check: a finance user selects the payee and bank account, enters the amount and other check details, and saves the check.
4. Review and approve: when approval rules apply, designated approvers review the check and approve or reject it. Approval permissions can be separated from check creation and printing.
5. Print: an authorized user generates the check output and prints it using the configured layout and printer settings. The system includes print calibration and print-batch support.
6. Track the record: users with access can review check status, register information and history. Changes and important actions are recorded for traceability.

Bank account details are protected in storage and masked in normal application responses. Full account numbers are not displayed in routine screens.

# Time and Attendance workflow
1. Configure the organization: a manager maintains employee profiles, workspaces or work locations, attendance settings and schedule information.
2. Employee clocks in or out: the employee uses the attendance web application, which can request the device's location and camera access.
3. Location check: when geofencing is configured, the system compares the reported location with the workspace's allowed area. The result can identify an on-site, late or out-of-area punch according to the configured rules.
4. Identity check: the attendance service can compare a live camera capture with the employee's stored reference photo and run liveness checks before accepting a punch.
5. Manager review: authorized managers can review records and summaries, filter by employee, date or status, and export reports.

Location and camera checks depend on device permissions, network availability and the organization's configured policies. The facial liveness checks are software-based safeguards. They should not be treated as certified identity proof or as a guarantee against every spoofing attempt.

# Data protection and accountability
- Organization separation: business records are associated with an organization and, where applicable, a workspace. Access checks are applied before users can work with protected records.
- Role-based access: permissions control sensitive actions such as managing bank accounts, approving checks, printing checks or managing members.
- Secure sign-in: passwords are stored as protected password hashes. Sessions use short-lived access credentials with refresh handling.
- Financial data: bank routing and account information is encrypted in storage and masked in ordinary responses.
- Activity history: important business changes and approval transitions create audit records, supporting later review.
- Input and service protections: services validate incoming data and apply request limits and centralized error handling.

Production security also depends on deployment configuration, access administration, backups, network controls and operational procedures.

# Getting started (onboarding)
1. Confirm the organization structure, locations, users and responsibilities.
2. Agree which products and workflows are in scope.
3. Configure the organization, workspaces, roles and permissions.
4. For Check Printing, configure bank accounts, payees, approval rules, check design and printer calibration.
5. For Time and Attendance, configure employees, locations, schedules, geofence boundaries and camera and location policies.
6. Train administrators, finance users, approvers, managers and employees.
7. Validate the agreed workflows in your own environment before operational use.

Final workflows, subscription terms, attendance policies, approval thresholds, printer hardware, hosting, backups and data-retention periods are agreed during implementation and onboarding.

# FAQ
Q: Can different people create, approve and print a check?
A: Yes. Creating, approving and printing a check can be assigned to different people through permissions.

Q: Are full bank account numbers shown in the application?
A: No. Bank account details are encrypted in storage and masked in normal screens and responses.

Q: Does attendance use location?
A: When geofencing is configured, the system compares the employee's reported location with the workspace's allowed area to flag on-site, late or out-of-area punches.

Q: Is camera verification required for attendance?
A: It is optional and depends on the organization's policy. When used, a live capture is compared with the employee's reference photo with liveness checks. These are software safeguards, not certified identity proof.

Q: Can one company use more than one location?
A: Yes. The products are designed for organizations with one or more workspaces or locations.

Q: Do I need separate logins for Check Printing and Time and Attendance?
A: No. Both products use one shared organization account foundation.
