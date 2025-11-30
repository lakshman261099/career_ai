# 🏫 CareerAI --- Admin & Multi-Tenant Specification

## 1. Roles

### Super Admin

-   Manage universities\
-   Global usage\
-   Billing + logs\
-   Queue monitoring

### University Admin

-   Manage students\
-   Add/bulk credits\
-   Upload CSV\
-   View usage\
-   Billing history

### Student

-   Uses tools (if verified + has credits)

------------------------------------------------------------------------

## 2. University Lifecycle

### Creation

-   Super Admin creates OR\
-   Signup → pending approval

### Data Fields

-   Name\
-   Subdomain\
-   Allowed domains\
-   Initial credits

### After Approval

-   University admins login via subdomain

------------------------------------------------------------------------

## 3. Admin Panels

### Super Admin

-   Universities list\
-   Students (all)\
-   Credits management\
-   AI usage\
-   CSV export\
-   Queue health\
-   Error logs

### University Admin

-   Students list\
-   Credit allocation\
-   CSV upload\
-   Usage charts\
-   Plan & billing

------------------------------------------------------------------------

## 4. Tenancy Enforcement

### Subdomain Routing

    sub = request.host.split('.')[0]
    tenant = University.query.filter_by(subdomain=sub).first()

### Scoped Queries

`model.query.filter_by(university_id=current_tenant.id)`

### Global Users

Use main domain `careerai.app`

------------------------------------------------------------------------

## 5. Permissions

  Action                Super Admin   Univ Admin   Student
  --------------------- ------------- ------------ ---------
  Manage universities   ✔             ✖            ✖
  Manage students       ✔             ✔            ✖
  Add credits           ✔             ✔            ✖
  Run tools             ✔             ✔            ✔
  View usage            ✔             ✔            limited

------------------------------------------------------------------------

# End
