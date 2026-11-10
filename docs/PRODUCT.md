# Tiimdi: product notes, roles, rules

## Audience (by priority)
1. **Student / regular user:** map, nearby deals, search, saved deals, price confirmation, points.
2. **Business (venue):** dashboard, own deals, menu from a photo, analytics (views, routes), Free/Pro subscription.
3. **Moderator:** reviews new venues, menus and deals, handles complaints about reviews and deals.
4. **Admin:** assigns moderators, sees system health.

## Rules
- **Saved and finished deals:** a finished deal stays in "Saved" but turns grey with an "Offer ended" badge. It disappears automatically 3 days after ending, or can be removed manually. Implemented in the UI.
- **Where venues come from:** (1) a venue registers itself and a moderator confirms it; (2) initial data from open sources such as OpenStreetMap (ODbL license, attribution required).
- **Photos:** the `venues.photo_url` column exists; if there is a photo it is shown on the card, otherwise a colored cover with an icon. Photo upload comes with the business dashboard (requires login).
- **Moderation:** deals already have `deals.status` (active / moderation / rejected). New deals from new venues will start in `moderation` (planned).
- **Points:** currently calculated in the browser; to be moved to the server (a ledger table plus a Redis sorted set for the leaderboard).

## Map
Restricted to Almaty and its surroundings via `maxBounds` in `static/index.html`. Flat from afar, 3D buildings from zoom 16.

## Next steps
1. Server-side login (JWT), roles, venue owner.
2. Move points and saved deals to the database.
3. Moderation queue and complaints.
4. Venue photo upload.
5. Subscriptions, analytics, ML ranking, deployment.

## Role selection and why a school student cannot become a moderator
- On first launch the user picks only: **student, school student, regular user**, or **"I represent a venue"**. Gender is optional.
- **Moderator and admin are not selectable.** Their accounts are created by an admin only (invite or DB record), with password login (JWT) and later 2FA. The server never accepts a role from the client: the role comes from the token.
- **Business:** a request (name, address, phone) goes to the `business_requests` table with status `pending` (3 requests per hour per address). A moderator verifies the venue exists and grants the role. Until then the person stays a regular user.
- **Abuse protection:** Redis rate limits (already on deals, menus, reviews, requests); before public deployment, Cloudflare (free plan) and an HTTPS proxy go in front.
- There is no server-side login yet: the venue/moderator/admin role is enabled for development only via `?debug` in the URL, which will be removed before deployment.

## Reviews
The `reviews` table (rating 1-5, text up to 500 characters, status visible/hidden). Limit: 5 reviews per minute. Moderators will hide reviews (dashboard, next step). Until login exists, the author name comes from the profile stored on the device.
