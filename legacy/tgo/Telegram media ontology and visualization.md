# Extending TGO to cover every Telegram entity

TGO, the Telegram Graph Ontology sketched in the earlier report, already carries a six-layer backbone (Core, Content, Network, Economy, Derived, Provenance), a strict rule that a Telegram account is never a person, and deterministic node keys. This report extends that backbone to **fully cover people, every kind of grouping of people, and every media and file type Telegram can carry**, and then recommends a visualization stack. The decisive findings: the live MTProto schema is **layer 230**, not the 225 shown on the public docs, and it already contains **Communities** (a group-of-groups container, `community#65efe954`), so both must be modeled as first-class ([api.tl layer 230](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl)); a Telegram **photo has no original-bytes rendition** (only server-resized JPEGs), so compressed photos must be fingerprinted perceptually while **documents keep original bytes** and support sha256 identity and metadata extraction ([core: files](https://core.telegram.org/api/files)); people links stay **scored candidate edges reusing nomenklatura's judgement model and FtM's `Similar` schema**, never `owl:sameAs`; and the earlier report's "shared sticker-set authorship" person link is **dropped** as unverified and de-anonymizing. The recommended open-source extraction backbone is hashing (sha256, `file_unique_id`, PDQ) plus gated OCR, ASR (faster-whisper), Apache Tika and metadata tools, with **face recognition, speaker matching, and the sticker-set-ID creator trick deliberately excluded** on GDPR Art. 9 and EU AI Act Art. 5 grounds. For visualization, **sigma.js v4 + graphology (MIT)** and **cosmos.gl (MIT)** are the open WebGL anchors, Gephi 0.11 the desktop anchor, with **Graph in Microsoft Fabric (reported GA June 2026, secondary source)** and Power BI offered as the Microsoft-stack complements the lead already uses.

Throughout, every modeled fact keeps the TGO epistemic split from L6: **observed** (platform metadata, EXIF as read, OCR text as read), **file-declared** (XMP, OOXML docProps, ID3, forgeable), and **inferred** (every ML output and every person link), each written as a `tg:Assertion` with `tg:confidence`, `tg:epistemicStatus` and PROV-O provenance. Namespaces follow the earlier report: `tg:` = `https://{{YOUR_DOMAIN}}/ontology/tg#`, instances in `tgr:`.

---

## People are modeled as accounts plus scored, never-merged person judgements

The richest person surface is MTProto `user` + `userFull`, about 50 `user` fields and about 60 `userFull` fields in layer 230, far more than the Bot API `User`/`ChatFullInfo` subset or the Desktop export (which gives only the exporter's own profile, contacts, frequent contacts and sessions) ([api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl); [core userFull](https://core.telegram.org/constructor/userFull)). Ingestion must key on **constructor and field names, not CRC32 IDs**, because the documented `user#31774388` (layer 225) is `user#b1b8cc83` in layer 230, which adds `bot_guard` and `linked_community_id` ([api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl)).

### Refined and new person-side classes

| Class | Super / alignment | Epistemic status | Key properties |
|---|---|---|---|
| `tg:Account` (refined) | `foaf:OnlineAccount`, `ftm:UserAccount` (`service`="telegram") | observed | `tg:userId`, `tg:isBot`, `tg:isDeleted` (→ `as:Tombstone`), `tg:isPremium`, `tg:isVerified`, `tg:isScam`, `tg:isFake`, `tg:langCode`, `tg:registrationMonth`, `tg:phoneCountry`, `tg:nameChangeDate`, `tg:photoChangeDate` |
| `tg:HumanAccount` / `tg:BotAccount` / `tg:DeletedAccount` (existing) | `tg:Account` | observed | bot flags (`tg:botActiveUsers`, `tg:botManagerId`, `tg:botVerification`) on BotAccount |
| `tg:Username` (refined) | `skos:Concept` notation | observed | `tg:usernameValue`, `tg:editable`, `tg:active`, `tg:expired`, `tg:deletable`, `time:hasBeginning`/`time:hasEnd` (collectible usernames transfer, so a username identifies an account at a time) |
| `tg:ProfileSnapshot` (new) | `as:Profile` (`as:describes` → Account) | observed | `tg:about` (bio), `tg:firstName`, `tg:lastName`, `tg:emojiStatus`, `tg:birthday`, `tg:observedAt`; one per `userFull` fetch, because `userFull` is a snapshot |
| `tg:ContactAnnotation` (new) | `oa:Annotation`, `prov:wasAttributedTo` collector | observed (collector-authored) | `tg:note`, `tg:personalPhoto`, contact-assigned `tg:firstName`/`tg:lastName`; scoped to the collecting account so it never leaks into the target's self-presentation |
| `tg:Person` (existing, inferred only) | `foaf:Person`, `ftm:Person` | inferred | minted only on a positive identity judgement; `owner` of `ftm:UserAccount` filled only then |
| `tg:PersonCluster` (existing, inferred only) | `prov:Entity` | inferred | canonical `tgr:PC-{{shortuuid}}` IDs, minted on first positive judgement (nomenklatura pattern); `tg:operatedBy` links accounts |
| `tg:IdentityJudgement` (new) | `prov:Entity`; serializes to `ftm:Similar` | inferred | reuses nomenklatura `Resolver` columns verbatim (below) |

Every **viewer-relative** field (`contact`, `mutual_contact`, `close_friend`, `blocked`, `note`, `personal_photo`, `common_chats_count`, `stories_hidden`) is an Observation whose subject is the pair (collecting account, target account), carrying `tg:observedBy` = the collecting `tg:Account`; otherwise two collectors' views appear to contradict each other (inference from the data model). An **absent field is not absence**: model a `tg:FieldVisibility` (visible | hidden_by_privacy | not_set | not_requested | min_object) per Observation, because `userFull.fallback_photo` and Bot API `MessageOriginHiddenUser` are positive evidence of a privacy rule, not missing data ([core userFull](https://core.telegram.org/constructor/userFull); [Bot API spec](https://github.com/PaulSonOfLars/telegram-bot-api-spec)).

### Person attribute field catalog (where each attribute comes from)

| Attribute | MTProto (layer 230) | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Numeric ID | `user.id` | `User.id` | `personal_information.user_id`, message `from_id` |
| First / last name | `user.first_name` / `last_name` | `User.first_name` / `last_name` | `personal_information`, message `from` |
| Username(s) | `user.username`, `user.usernames:Vector<Username>` | `User.username`, `ChatFullInfo.active_usernames` | message `from` handle |
| Phone | `user.phone` (privacy-gated) | not exposed | `personal_information.phone_number`, `contacts.list[].phone_number` |
| Bio / about | `userFull.about` | `ChatFullInfo.bio` (private chats) | `personal_information.bio` |
| Birthday | `userFull.birthday:Birthday` | `ChatFullInfo.birthdate` | not exposed |
| Profile photo history | `photos.getUserPhotos` → `photo{date,sizes,video_sizes}` | `getUserProfilePhotos` | `profile_pictures[]` (own only) |
| Presence / last seen | `user.status:UserStatus` | not exposed | not exposed |
| Premium, emoji status, peer color | `user.premium`, `emoji_status`, `color`, `profile_color` | `User.is_premium`, `ChatFullInfo.emoji_status_custom_emoji_id` | not exposed |
| Personal channel | `userFull.personal_channel_id` | `ChatFullInfo.personal_chat` | not exposed |
| Business (hours, location, intro) | `userFull.business_*` | `ChatFullInfo.business_*` | not exposed |
| Saved / profile music | `users.getSavedMusic`, `userFull.saved_music` | `getUserProfileAudios` (9.4) | `profile_music[]` |
| Account provenance | `peerSettings.registration_month`, `phone_country`, `name_change_date`, `photo_change_date` | not exposed | not exposed |
| Contacts, close friends, top peers | `contacts.getContacts`, `editCloseFriends`, `getTopPeers` | not exposed | `contacts`, `frequent_contacts` |
| Sessions (self only) | account methods | not exposed | `sessions` (`last_ip`, `last_country`, device) |

Sources for the whole table: [api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl), [Bot API spec 10.3](https://github.com/PaulSonOfLars/telegram-bot-api-spec), [export_output_json.cpp](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/export/output/export_output_json.cpp). The provenance fields `registration_month` + `phone_country` + `name_change_date` + `photo_change_date` are strong sockpuppet and coordination features (account age, country, recent rename) and are personal data (phone country is quasi-location) (inference).

### Relationship signals between people, in three observability tiers

The research inventoried about 40 person-to-person signals. They fall into three tiers by who can see them, which decides evidentiary weight:

| Tier | Examples (edge → TGO) | Who observes |
|---|---|---|
| **Viewer-only (ego network of the collector)** | contacts (`KNOWS`), close friends (`CLOSE_FRIEND_OF`), blocked, frequent contacts (weighted tie), contacts' birthdays | the collecting account only |
| **Pairwise (any viewer)** | common chats (`CO_MEMBER`), gifts on profile (`GAVE_GIFT_TO`, dated, with optional message, hidden when `name_hidden`), story reposts (`fwd_from` → `REPOSTED`), story mentions (`MENTIONS`), forwards (`FORWARDED` + original author signature), replies/threads (`REPLIES_TO`), reactions (`REACTED`, timestamped), non-anonymous poll votes (`VOTED`), personal channel (`FEATURES`), business delegation (`DELEGATES_TO` a bot), third-party verification (`VERIFIES`) | anyone who can view the profile or chat |
| **Admin / owner only** | inviters (`INVITED_BY`), approvers (`APPROVED_BY`), promoters/kickers, boosters, top inviters/posters (`stats.getMegagroupStats`), join requesters, story viewers/reactors of your own stories, monoforum DM senders | admins/owner; flag these Observations with elevated sensitivity because they reveal audience members who did not publicly post |

All signatures: [api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl); Bot API reaction/member/join updates require the bot to be admin and to request them ([Bot API spec](https://github.com/PaulSonOfLars/telegram-bot-api-spec)). Gift edges, story-repost edges and personal-channel edges are the **most valuable new person-to-person signals since 2024** because they are visible to arbitrary viewers (inference). Gift-sender edges can be retroactively hidden (`name_hidden`, paid signature removal, Bot API `UniqueGiftInfo.is_private`), so store them as time-bounded Observations and never overwrite history ([Bot API changelog](https://core.telegram.org/bots/api-changelog)).

Fourteen privacy key pairs gate the data (`StatusTimestamp, ChatInvite, PhoneCall, PhoneP2P, Forwards, ProfilePhoto, PhoneNumber, AddedByPhone, VoiceMessages, About, Birthday, StarGiftsAutoSave, NoPaidMessages, SavedMusic`), with twelve rule types; only the collector's **own** `account.getPrivacy` returns rules, and those rules are themselves an analyst-side grouping of people ([core privacy](https://core.telegram.org/api/privacy)).

### Reusing nomenklatura's judgement model and FtM Similar for identity

The TGO rule that an account is never a person is enforced by making every account-to-person and account-to-account identity claim a **judgement**, reusing the nomenklatura `Resolver` (MIT) almost verbatim ([resolver.py](https://github.com/opensanctions/nomenklatura/blob/main/nomenklatura/resolver/resolver.py); [judgement.py](https://github.com/opensanctions/nomenklatura/blob/main/nomenklatura/judgement.py)):

| `tg:IdentityJudgement` property | From nomenklatura | FtM `Similar` serialization |
|---|---|---|
| `tg:source` (Account or PersonCluster IRI) | `source` | `candidate` |
| `tg:target` | `target` | `match` |
| `tg:judgement` ∈ {positive, negative, unsure, no_judgement} | `Judgement` enum | (positive → `Similar` asserted) |
| `tg:score` [0,1] | `score` (nullable) | `confidenceScore` |
| `tg:decidedBy` (analyst id or `"splink-v5:run-{{N}}"`) | `user` (required) | `matcher` |
| `tg:createdAt` / `tg:deletedAt` (soft delete) | `created_at` / `deleted_at` | `modifiedAt` |
| `tg:evidence` (list of Observations) | (TGO addition) | `criteria` |
| `tg:legalBasis` | (TGO addition) | (none) |

Mechanics: algorithm suggestions (Splink match probability, MIT, Fellegi-Sunter at scale, [Splink](https://github.com/moj-analytical-services/splink)) write `no_judgement` rows with the probability as `score`; only analysts (or a documented auto-accept policy above a threshold) write `positive`; a positive decision between two non-canonical IDs **mints a new `tgr:PC-{{shortuuid}}`** and links both (nomenklatura's `NK-` pattern); `negative` acts as a cannot-link constraint blocking transitive merges; `explode` un-merges for GDPR rectification without deleting Observations. Evidence is non-biometric: shared phone hash, same personal channel, same bio text, username history, co-admin, gift edges, shared avatar **perceptual** hash (never face embeddings). Keep everything MIT (nomenklatura + Splink); avoid Zingg (AGPL-3.0) in a hosted service ([Zingg](https://github.com/zinggAI/zingg)).

Person-to-person relations serialize to FtM `Associate` with `relationship` codes such as `telegram_mutual_contact` or `telegram_gift_sender`; `Family` is emitted only from explicit evidence, never derived from Telegram signals alone; and FtM `Person.religion`/`political`/`ethnicity` are blocked from automatic population by inference (Art. 9) ([FtM schema](https://github.com/opensanctions/followthemoney/tree/main/followthemoney/schema)).

---

## Groupings span native containers, real-world orgs, inferred clusters and analyst cases

Telegram has at least 20 native groupings. The decisive correction to the earlier report: **Communities already exist in MTProto layer 230** as `community#65efe954 creator left min collapsed_in_dialogs id access_hash title photo date admin_rights default_banned_rights`, with `communityFull.linked_peers:Vector<CommunityPeer>`, methods `communities.create/togglePeerLink/getJoinedCommunities/getParticipantJoinedChats`, and `user.linked_community_id` / `channel.linked_community_id` ([api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl)). Communities were announced 2026-07-14: "Several groups, channels, and bots can now be linked together into communities around a shared topic" ([Telegram blog](https://telegram.org/blog/communities-editor-invisible-messages)), and the Bot API reached `CommunityChatJoined` in 10.3 ([Bot API changelog](https://core.telegram.org/bots/api-changelog)).

### Group class hierarchy (native, real-world, inferred, analyst)

| Class | Super / alignment | Epistemic status | Key properties |
|---|---|---|---|
| `tg:Group` (abstract) | `foaf:Group`, `as:Group` | n/a | `tg:groupKind` (SKOS), `tg:label` |
| `tg:PlatformGroup` | `tg:Group` | observed | links to `tg:Chat`/`tg:Community`/list; `tg:visibilityScope` (public \| members \| admins \| owner_only \| link_holders) |
| `tg:Community` (new, refined) | `tg:PlatformGroup`, `org:FormalOrganization` | observed | `tg:communityId`, `tg:hidden`; `org:hasSubOrganization` → linked chats; `tg:CommunityPeerLink {visible, can_view_history, requested_by, date}` |
| `tg:RealWorldGroup` | `tg:Group`, `org:Organization` | asserted (with evidence) | subclasses `tg:FormalOrganization` (`org:FormalOrganization`), `tg:OrgUnit` (`org:OrganizationalUnit`), `tg:Network`/`tg:Movement` (`org:OrganizationalCollaboration`), `tg:Family`, `tg:Household` |
| `tg:InferredGroup` | `tg:Group`, `prov:Entity` | inferred | subclasses `tg:CommunityCluster`, `tg:CoordinationCluster`, `tg:OperatorCluster`, `tg:SockpuppetCluster`, plus existing `tg:PersonCluster` |
| `tg:CaseGroup` | `tg:Group` | analyst-defined | `prov:wasAttributedTo` analyst, `tg:rationale`, `tg:caseId`, `tg:legalBasis` |
| `tg:GroupMembership` | `org:Membership`, `tg:Assertion` | per instance | `org:member`, `org:organization`/`tg:group`, `org:role` (SKOS), `org:memberDuring` (`time:Interval`), `tg:membershipScore`, `tg:assertionStatus`, `prov:wasGeneratedBy` |
| `tg:DetectionRun` | `prov:Activity` | n/a | `tg:algorithm` (SKOS), `tg:softwareAgent`, `tg:parameters` (JSON), `prov:used` snapshot, `tg:qualityScore`, start/end |
| `tg:GraphSnapshot` | `prov:Entity` | derived | `tg:edgeType`, `tg:weightDefinition`, time window, `tg:nodeFilter`, `prov:wasDerivedFrom` Observations |
| `tg:ClusterLineage` | `org:ChangeEvent` | inferred | `org:originalOrganization`, `tg:lineageType` (continue, split, merge, birth, death), `tg:jaccard` |

### Native grouping observability matrix (abbreviated)

| Grouping | Key constructors | MTProto user account | Bot API | Desktop export |
|---|---|---|---|---|
| Basic group | `chat`, `chatFull.participants`, `chatParticipant{user_id,inviter_id,date,rank}` | full member list + inviter if member | no enumeration; `getChatMember(s)` | `private_group`; service actions |
| Supergroup / gigagroup | `channel megagroup gigagroup forum`, `channels.getParticipants` (filters) | member list unless `participants_hidden` (then admins/bots) | `getChatMember(s)`; `chat_member` updates if admin | `private/public_supergroup` |
| Broadcast channel subscribers | `channel broadcast`, `channelParticipant.subscription_until_date` | admins only (`can_view_participants`) | admin bot `chat_member` only | `private/public_channel` |
| Forum topics | `forumTopic{id,title,icon_emoji_id,from_id}` | topic creator + posters | `message_thread_id`, `ForumTopicCreated` | `topic_created` |
| Monoforum (channel DMs) | `channel.monoforum`, `monoForumDialog` | admins see every DM sender as a topic | admin bot | `monoforum` key |
| **Communities** | `community`, `communityFull`, `communityPeer` | members see linked chats; admins see per-participant joined/created | 10.2–10.3 `Community`, `CommunityChat*` | `join_group_via_community` |
| Chatlists (shareable folders) | `chatlists.exportChatlistInvite` | link holders see the peer set | `via_chat_folder_invite_link` | n/a |
| Group calls / conferences | `groupCall`, `phone.getGroupParticipants` | participants unless `listeners_hidden` | n/a | `group_call` actions |
| Giveaways / winners | `messageMediaGiveaway(Results)`, `GiveawayWinners` | winners if public | yes | `giveaway_launch/results` |
| Boosters | `boost`, `premium.getBoostsList` | admins; own boosts | admin bot | `boosts` key |

Sources: [api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl), [Bot API spec](https://github.com/PaulSonOfLars/telegram-bot-api-spec), [export_output_json.cpp](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/export/output/export_output_json.cpp). Model each native grouping as a `tg:PlatformGroup` with `tg:groupKind` from a SKOS scheme and record `tg:visibilityScope` on each membership Observation, because the same membership fact carries different evidentiary weight depending on how it was seen (inference). A participant's `joined_chat_ids` vs `creator_chat_ids` in a Community is a ready-made joiner-vs-creator role split ([TDLib td_api.tl](https://github.com/tdlib/td/blob/master/td/generate/scheme/td_api.tl)).

### Inferred groups: detection runs with parameters, quality and stability

Keep three disjoint group families with different epistemic status: real-world (asserted with evidence), inferred (algorithmic, with run + parameters + score + stability), and analyst case groups (`prov:wasAttributedTo` an analyst). Coordination detection maps cleanly onto the **CooRTweet (MIT)** "shared object within a time window" abstraction ([CooRTweet](https://github.com/nicolarighetti/CooRTweet)): `object_id` = original message key (`fwd_from.from_id` + `channel_post`) for co-forwarding, or a SimHash of normalized text, or a canonical URL, or a `tg:MediaFingerprint` pHash; `account_id` = posting channel id; `content_id` = `(peer_id, msg_id)`; `timestamp_share` = message date. `detect_groups(x, time_window, min_participation, remove_loops)` returns account pairs sharing objects within the window ([detect_groups.R](https://github.com/nicolarighetti/CooRTweet/blob/master/R/detect_groups.R)).

| Detection algorithm (SKOS `tg:algorithm`) | Tool | License | Use |
|---|---|---|---|
| `leiden_cpm` / `leiden_modularity` | leidenalg | GPL-3.0 | community detection ([leidenalg](https://github.com/vtraag/leidenalg)) |
| `louvain` / `infomap` / `kcore` | python-igraph | GPL-2.0 | community detection ([python-igraph](https://github.com/igraph/python-igraph)) |
| `louvain` (BSD path) | NetworkX / graphology-communities-louvain | BSD-3 / MIT | in-browser or Python ([NetworkX](https://github.com/networkx/networkx)) |
| `coortweet` | CooRTweet | MIT | coordination (co-forward, near-dup, co-URL, co-fingerprint) |
| `nomenklatura_xref` / `splink` | nomenklatura / Splink | MIT / MIT | operator and person clustering |
| GDS Louvain/Leiden/WCC/PageRank | Neo4j GDS Community | GPL-3.0 (4-core cap) | server-side ([GDS](https://neo4j.com/docs/graph-data-science/current/introduction/)) |

Store per cluster a `tg:stabilityScore` (best-match Jaccard across reruns with different seeds or bootstrap resamples) and per membership a `tg:coAssignmentFrequency` (share of runs in which the member landed in the matched cluster); promote only clusters above a stability threshold to analyst view (design proposal, not a sourced standard). **Operator (same-admin) clusters** should combine non-biometric signals: `post_author`/signature equality, `channels.getMessageAuthor`, shared admins, shared linked discussion group, shared `personal_channel_id`, shared giveaway `channels` vectors, co-membership in a Community created by the same account, shared creation dates and naming patterns, and shared promoted domains (the DFRLab evidence stack, below).

### How OSINT practitioners actually group Telegram actors (prior art to mirror)

DFRLab's 2025 Moldova investigation grouped **39 channels named after administrative regions, reportedly all created May 2024**, with "over 2,500 identical messages" and "nearly 12,000 posts synchronized to the seconds", shared translation errors, AI-generated profile pictures, and a shared website tied via WHOIS to a Moscow company ([DFRLab 2025](https://dfrlab.org/2025/03/06/telegram-network-moldova/)). The 2023 pro-Kremlin study built a graph of **3,871 channel nodes and 57,646 forwarded-message edges**, ran Gephi modularity to nine clusters, and used TGStat name history to link renamed channels ([DFRLab 2023](https://dfrlab.org/2023/03/01/networks-of-pro-kremlin-telegram-channels-spread-disinformation-at-a-global-scale/)). Bovet & Grindrod found upstream/core/downstream community roles via random-walker flow clustering ([ORA Oxford](https://ora.ox.ac.uk/objects/uuid:f5717551-cb03-4d6e-9aa0-6c3a41228948)). This maps to TGO SKOS concepts (`named_channel_network`, `regional_channel_franchise`, `originator`, `amplifier`, `bridge`/`hub`, `upstream`/`core`/`downstream`, `official_state_amplifier`, `operator_cluster`) and edge types (`FORWARDED`, `IDENTICAL_POST`, `NEAR_DUP_POST`, `SYNC_POST`, `SAME_CREATION_DATE`, `NAME_HISTORY_LINK`, `SHARED_DOMAIN_PROMOTION`, `CO_SPONSOR_GIVEAWAY`, `LINKED_DISCUSSION`, `SAME_COMMUNITY`), each an Observation; the network claim is an Assertion with combined confidence (inference, matching DFRLab's evidence stacking to TGO L6).

### Membership modeling with time and roles

Use W3C ORG `org:Membership` as the n-ary core (member, organization, role, `org:memberDuring` with OWL-Time) ([W3C ORG](https://www.w3.org/TR/vocab-org/)), FtM interval schemata for OSINT-compatible export (all carry `startDate`/`endDate` plus `sourceUrl`/`proof`), and ActivityStreams `Join`/`Leave`/`Invite`/`Block` for event semantics ([FtM Interval](https://followthemoney.tech/explorer/schemata/Interval/); [AS Vocabulary](https://www.w3.org/TR/activitystreams-vocabulary/)). The `org:role` SKOS scheme `tg:GroupRole` covers: creator, owner, admin, member, restricted, banned, left, subscriber, booster, giveaway_winner, call_participant, speaker, inviter, approver, originator, amplifier, bridge, hub, core, peripheral. In Neo4j, use reified `(:GroupMembership)` nodes for inferred and analyst memberships (they need evidence and run links) and plain `[:MEMBER_OF {role, since, until, obs_id}]` relationships for high-volume observed platform memberships, mirroring ORG's direct-shortcut pattern.

---

## Every media and file type gets a class, a rendition model and a field catalog

Telegram splits media into two storage regimes with a crucial consequence for TGO. A **`photo`** is an immutable server object with a list of server-generated JPEG renditions (`sizes`) and optional `video_sizes`; **there is no original-bytes rendition**, so a Photo is a set of derived JPEGs, its largest size is server re-encoded, and its sha256 will differ from the uploader's file ([core: files](https://core.telegram.org/api/files)). Everything else (files, video, GIF, audio, voice, round video, stickers, custom emoji, wallpapers, themes, ringtones, saved music) is a **`document`** with `mime_type`, 64-bit `size` and a `DocumentAttribute` vector that determines its type; **documents preserve original bytes**, so sha256 identity and embedded-metadata extraction are meaningful for them ([api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl)).

### Media class hierarchy and renditions

| Class | Alignment | Classification rule (from `DocumentAttribute`) | Keying |
|---|---|---|---|
| `tg:Photo` | `schema:ImageObject`, `ma:Image`, `nfo:RasterImage` | `messageMediaPhoto` | `photo:{id}` |
| `tg:PhotoRendition` | (rendition of ImageObject) | per `PhotoSize.type` letter | `photo:{id}:{sizeType}` |
| `tg:LivePhoto` (new) | composite | `messageMediaPhoto.live_photo` + `video:Document` | `tg:hasStill` + `tg:hasMotionClip` |
| `tg:Video` | `schema:VideoObject`, `ma:MediaResource` | `documentAttributeVideo` (not round, not sticker/animated) | `media:{file_unique_id}` or `file:{sha256}` |
| `tg:RoundVideo` | `schema:VideoObject` | `documentAttributeVideo.round_message` | as video |
| `tg:Animation` (GIF) | `schema:VideoObject` | `documentAttributeAnimated` | as video |
| `tg:VideoVariant` (new) | alternate encoding | `alt_documents` split by MIME | `tg:variantOf`, `tg:codec`, w, h |
| `tg:HlsPlaylist` / `tg:Storyboard` / `tg:StoryboardMap` (new) | auxiliary docs | `alt_documents` MIME `x-mpegurl` / `x-tgstoryboard` / `x-tgstoryboardmap` | auxiliary |
| `tg:Audio` | `schema:AudioObject` (+ `schema:MusicRecording` for music) | `documentAttributeAudio` (voice unset) | as document |
| `tg:VoiceNote` | `schema:AudioObject` | `documentAttributeAudio.voice` | as document |
| `tg:Sticker` | `schema:ImageObject`/`VideoObject` | `documentAttributeSticker` | `media:{file_unique_id}` |
| `tg:CustomEmoji` | `schema:ImageObject` | `documentAttributeCustomEmoji` | document id |
| `tg:Document` (file) | `schema:MediaObject`/`DigitalDocument`, `nfo:FileDataObject` | else | `file:{sha256}` |
| `tg:File` (bytes) | `nfo:FileDataObject`, PREMIS File | n/a | primary `file:{sha256}`; alt `media:{file_unique_id}`; never `file_id` |
| `tg:MediaAttachment` (new) | occurrence-in-message | spoiler, TTL, view-once, cover, timestamp | per (message, media) |

Classification order matters: check Sticker and CustomEmoji, then round/voice/animated flags, then Video/Audio, then Document, because a GIF and a video sticker both also carry `documentAttributeVideo` ([TDLib MessageContent.cpp](https://github.com/tdlib/td/blob/master/td/telegram/MessageContent.cpp)). Model spoiler, TTL (`0x7FFFFFFF` = view-once) and view-once as properties of `tg:MediaAttachment`, not of the Photo/Document, because the same document can be resent without them ([core: messageMediaDocument](https://core.telegram.org/constructor/messageMediaDocument)). Attributes are **sender-declared** (Bot API: "as defined by the sender"), so `duration`, `w`, `h`, `title`, `performer`, `file_name` carry `tg:declaredBySender`, not `tg:measured` ([Bot API spec](https://github.com/PaulSonOfLars/telegram-bot-api-spec)).

**PhotoSize type letters** (verbatim from [core: files](https://core.telegram.org/api/files)): `s` 100x100, `m` 320, `x` 800, `y` 1280, `w` 2560 (bounded); `a`–`d` cropped squares; `i` stripped inline thumbnail; `j` vector SVG outline. Stripped (`i`) and path (`j`) renditions are inline base64 literals, not downloadable files (model as `tg:PhotoRendition` with inline value). **VideoSize letters**: `p`/`u` animated profile preview/full, `v` video preview, `f` premium sticker effect (TGS).

### Per-media-type field catalog (MTProto / Bot API / Desktop export)

**Photo**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Identity | `photo.id`, `access_hash`, `file_reference` | `PhotoSize.file_id`, `file_unique_id` (per size) | none (path only) |
| Renditions | `sizes:Vector<PhotoSize>` (w,h,size,type) | `photos: PhotoSize[]` | largest rendition `.jpg` |
| Dimensions | `photoSize.w/h` | `width`, `height` | `width`/`height` (if both nonzero) |
| Animated | `video_sizes:Vector<VideoSize>` | (via LivePhoto 10.0) | not exported |
| Upload date | `photo.date` | not on object | `photo` path mtime only |
| Spoiler / TTL | `messageMediaPhoto.spoiler`, `ttl_seconds` | `has_media_spoiler` | `media_spoiler`, `self_destruct_period_seconds` |

**Video / round video / animation**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Type flags | `documentAttributeVideo.round_message`, `supports_streaming`, `nosound` | `video` / `video_note` / `animation` objects | `media_type: video_file/video_message/animation` |
| Duration, w, h | `duration` (double), `w`, `h` | `duration`, `width`, `height`; `VideoNote.length` (diameter) | `duration_seconds`, `width`, `height` |
| Codec | `video_codec` (h264/h265/av1) | `VideoQuality.codec` | not exported |
| Alt qualities | `alt_documents:Vector<Document>` | `Video.qualities: VideoQuality[]` | not exported |
| Cover / start ts | `video_cover:Photo`, `video_timestamp` | `cover`, `start_timestamp` | not exported |

**Voice note / round video transcript**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Voice flag | `documentAttributeAudio.voice` | `Voice` object | `media_type: voice_message` |
| Waveform | `waveform` (5-bit packed, ~100 samples) | not exposed | not exported |
| Duration, mime | `duration`, `document.mime_type` | `duration`, `mime_type` | `duration_seconds`, `mime_type` |
| Transcript | `messages.transcribeAudio` → `transcribedAudio{text, pending, transcription_id}` | none | not exported |

**Audio / music**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Title / performer | `documentAttributeAudio.title/performer` | `Audio.title/performer` | `title`, `performer` |
| Album cover | document `thumbs` | `Audio.thumbnail` | `thumbnail` |
| Profile music | `users.getSavedMusic`, `userFull.saved_music` | `getUserProfileAudios` (9.4) | `profile_music[]` |

**Stickers / custom emoji**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Set | `documentAttributeSticker.stickerset`, `stickerSet{id,short_name,count}` | `Sticker.set_name`, `StickerSet` | `sticker_emoji` only |
| Format | mime `image/webp` / `x-tgsticker` / `video/webm` | `is_animated`, `is_video` | path extension |
| Custom emoji in text | `messageEntityCustomEmoji.document_id` | `custom_emoji_id` | entity in `text_entities` |
| Ownership | `stickerSet.creator` (own only); `_by_<bot>` suffix | none | none |

**Stories and MediaArea**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Story item | `storyItem{id,date,expire_date,media,media_areas,privacy,views,fwd_from,music}` | `Story{chat,id}` only | `stories[]` (media, no size) |
| Geo / venue area | `mediaAreaGeoPoint{geo,address}`, `mediaAreaVenue` | `StoryAreaTypeLocation`, address | not exported |
| Coordinates | `mediaAreaCoordinates{x,y,w,h,rotation,radius}` (percent, x/y = center) | `StoryAreaPosition` (percentages) | not exported |
| Channel-post / URL / gift areas | `mediaAreaChannelPost`, `mediaAreaUrl`, `mediaAreaStarGift` | `StoryAreaTypeLink`, etc. | not exported |

**Profile and chat photos, GIFs, wallpapers**

| Field | MTProto | Bot API 10.3 | Desktop export |
|---|---|---|---|
| Profile photo stub | `userProfilePhoto{photo_id, has_video, personal, stripped_thumb}` | `ChatPhoto`, `UserProfilePhotos` | `profile_pictures[]` (own) |
| Photo history | `photos.getUserPhotos` | `getUserProfilePhotos` | own only |
| Saved GIFs | `messages.getSavedGifs` (limit 200/400) | `Animation` object | path |
| Wallpaper / theme | `wallPaper`, `theme`, `wallPaperSettings` | `ChatBackground`, `BackgroundType*` | not exported |

All sources: [api.tl](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl), [Bot API spec 10.3](https://github.com/PaulSonOfLars/telegram-bot-api-spec), [core: files](https://core.telegram.org/api/files), [core: stories](https://core.telegram.org/api/stories), [export_data_types.cpp](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/export/data/export_data_types.cpp). Note the **export folder-vs-type mismatch**: GIFs and video stickers are written under `video_files/` while JSON says `media_type: animation`/`sticker`, so TGO import must classify from `media_type` + `mime_type`, not folder names (inference from tdesktop `DocumentFolder()` logic). Message media in the export has **no IDs**, so joining export records to MTProto/Bot data needs (chat, message id, date) or post-hoc sha256 of exported files.

### File identity and keys

Files upload in parts (512 KB max, 4000 parts default / 8000 Premium = 2000/4000 MiB), referenced by `id` + `access_hash` + volatile `file_reference` (expires in ~24-48h for user accounts, library claim not official), downloaded over typed `InputFileLocation` ([mtcute files](https://mtcute.dev/guide/topics/files); [core: files](https://core.telegram.org/api/files)). **Recommended TGO keys**: `tg:File` primary key `file:{sha256}` (computed after download, aligns `schema:sha256` / PREMIS fixity) or `media:{file_unique_id}`; secondary keys the Bot API `file_unique_id`; **never key on `file_id`, `access_hash` or `file_reference`** (account-scoped, volatile). Forwarding keeps the same document/photo `id` (the CDN treats "media forwarded from those channels" as the same cached file), so `document.id` is a valid cross-chat propagation key; a download-and-re-upload gets a new `id`, so TGO needs content fingerprints (sha256 for documents, perceptual hash for photos/video frames) to link re-uploads ([core: CDN](https://core.telegram.org/cdn)).

### Standards alignment for media (extends the earlier report's SIOC/FOAF/schema.org/PROV-O set)

| TGO / Telegram | schema.org | ma: (Media Resources) | NFO/NIE | PREMIS / other |
|---|---|---|---|---|
| tg:Photo / tg:PhotoRendition | ImageObject | ma:Image | nfo:RasterImage | |
| tg:Video/RoundVideo/Animation | VideoObject | ma:MediaResource + VideoTrack | nfo:Video | |
| tg:Audio / tg:VoiceNote | AudioObject (+MusicRecording) | ma:MediaResource + AudioTrack | nfo:Audio | |
| tg:Document | MediaObject / DigitalDocument | ma:MediaResource | nfo:FileDataObject / Document | |
| mime_type | encodingFormat | ma:hasFormat | nie:mimeType | |
| sha256 (computed) | sha256 | | nfo:FileHash | premis:Fixity (SHA-256) |
| chunk hashes (`getFileHashes`) | | | nfo:FileHash per chunk | premis:Fixity, originator "Telegram" |
| file_name | name | ma:title | nfo:fileName | premis:originalName |
| transcript | transcript | ma:hasCaptioning | | |
| video_timestamp / cover | startTime | ma:MediaFragment (`#t=`) | | |
| MediaArea coordinates | | ma:MediaFragment (spatial) | | Web Annotation `FragmentSelector xywh=percent:` + `SvgSelector` for rotation |

Sources: [schema.org MediaObject](https://schema.org/MediaObject), [W3C mediaont-10](https://www.w3.org/TR/mediaont-10/), [NFO](https://www.semanticdesktop.org/ontologies/2007/03/22/nfo/), [PREMIS 3](https://id.loc.gov/ontologies/premis-3-0-0.html), [W3C media-frags](https://www.w3.org/TR/media-frags/), [W3C annotation-model](https://www.w3.org/TR/annotation-model/). An unrotated MediaArea maps to `#xywh=percent:(x-w/2),(y-h/2),w,h`; rotation and radius need a Web Annotation `SvgSelector` alongside the `FragmentSelector` (inference). Percent coordinates stay valid across Telegram's resized photo variants, which differ in pixel size for the same `photo_id`.

---

## Extractors turn raw media into graph nodes, with licenses and throughput

Every extraction is modeled as a `prov:Activity` (also `mls:Run`) that `prov:used` the `tg:File`, is `prov:wasAssociatedWith` a `tg:ModelAgent` (tool + model + version + weights sha256 + code license + weights license), and emits bodies as `oa:Annotation`s with Media Fragment or TextPosition selectors. The following table is the recommended open-source extractor set, with what each yields for the graph.

| Extractor | Tool (version) | Code license | Weights license | Throughput (cited) | Graph yield |
|---|---|---|---|---|---|
| Exact / unique id | sha256, `file_unique_id` | n/a | n/a | seconds / ~1 min for 10k ([est.]) | `tg:sameFileAs` (forward/reuse chains) |
| Perceptual image hash | PDQ via `pdqhash` 0.2.8 | MIT bindings / BSD core | n/a | ~0.08 s/file worst case; Faiss match "up to 4,000 img/s" | `tg:nearDuplicateOf` (Hamming ≤31, quality ≥50) |
| Image near-dup index | Faiss `IndexBinaryFlat` 1.15.1 | MIT | n/a | 100k x 256-bit ≈ 3.2 MB, exhaustive fine | range search "all within 31" |
| Crop-robust copy detection | SSCD / CLIP | code MIT / weights unstated | varies | CLIP ViT-B-32 286 img/s on TITAN RTX | `tg:copyOf` (cosine ≥0.75); `tg:semanticallySimilar` (clustering only) |
| OCR (screenshots, memes) | PaddleOCR 3.7 PP-OCRv5 / RapidOCR 3.10 | Apache-2.0 | Apache-2.0 | mobile 0.46 s/img T4, 1.15-1.39 s CPU | `tg:Identifier` (wallet/phone/URL/@handle), `schema:embeddedTextCaption` |
| OCR fallback (CPU) | Tesseract 5.5.3 (`nor`/`rus`/`ukr`/`ara`) | Apache-2.0 | Apache-2.0 | slower; Arabic weak | same |
| Identifier validation | iocsearcher 3.1, phonenumbers 9.0, eth-utils, bip-utils | MIT / Apache / MIT | n/a | trivial | checksum-validated `tg:Identifier` (EIP-55, Base58Check, libphonenumber) |
| Document text + metadata | Apache Tika 4.1 `/rmeta` | Apache-2.0 | n/a | not measured; text extraction cheap | `tg:ExtractedText`, `tg:DocumentMetadata` (XMP IDs, author, producer) |
| High-value PDF layout/tables | Docling 2.14 | MIT | Apache-2.0 (TableFormerV2) | 0.92-2.45 pages/s CPU no-OCR | tables, reading order |
| Scanned PDF OCR | OCRmyPDF 17 (Tesseract) | MPL-2.0 | Apache-2.0 | ~30 s/page EasyOCR-class on CPU | text layer |
| Speech-to-text | faster-whisper 1.2 large-v3-turbo (+ NB-Whisper for Norwegian) | MIT | MIT / Apache-2.0 | 13 min audio in 16 s batched int8, RTX 3070 Ti | `tg:Transcript`, `tg:TranscriptSegment` (timed) |
| ASR (ru/uk/EU, fast) | NVIDIA Parakeet-TDT-0.6B-v3 | Apache-2.0 | CC-BY-4.0 | RTFx 3332 on A100; no ar/fa | same (routed by detected language) |
| Multilingual NER | spaCy 3.8 (ru/uk/nb/en) + Stanza (ar/fa) + GLiNER multi-v2.1 | MIT / Apache / Apache | MIT / Apache | fast | `tg:ExtractedEntity` with offsets |
| EXIF / XMP / C2PA | ExifTool 13.59 (subprocess), c2pa-python 0.38 | Perl / MIT OR Apache | n/a | minutes (documents only) | `tg:Device` (serial), `tg:GeoPoint` (GPS), `tg:Software`, C2PA signer |
| Audio class / reuse | PANNs CNN14 / YAMNet / Chromaprint 1.6.2 | MIT / Apache / MIT (LGPL as built) | CC-BY-4.0 / Apache | not measured | `tg:audioClass` (SKOS AudioSet), `tg:AudioReuse` |
| Keyframes / shots | PySceneDetect 0.7.2 + PyAV 19 | BSD-3 | n/a | not measured | keyframe anchors for CLIP/OCR/PDQ |
| Video near-dup | vPDQ (frame bags) / TMK+PDQF | BSD (uses LGPL ffmpeg) | n/a | 1 fps frames | `tg:nearDuplicateOf` with matched `#t=` fragments |
| Sticker render | rlottie-python 1.3.8 (TGS), ffmpeg (WEBM) | LGPL / LGPL | n/a | not measured | pHash of first frame; usage TF-IDF vector |
| Avatar ER (non-biometric) | SHA-256 + `file_unique_id` + PDQ, IDF down-weight | MIT/BSD | n/a | fast | `tg:MediaFingerprint` with `tg:hashClass`; IDF-weighted account link |
| Face detection (UI blur only) | deface 1.5 / YuNet (MIT) | MIT | MIT | SCRFD ~5 ms/VGA | **no linking edge**; blur mask only |

Sources for versions, licenses and figures: [pdqhash](https://pypi.org/project/pdqhash/), [PDQ README](https://raw.githubusercontent.com/facebook/ThreatExchange/main/pdq/README.md), [faiss-cpu](https://pypi.org/project/faiss-cpu/), [PaddleOCR docs](https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5.html), [iocsearcher](https://pypi.org/project/iocsearcher/), [Tika home](https://tika.apache.org/), [Docling tech report](https://arxiv.org/html/2408.09869), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Parakeet card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [exiftool.org](https://exiftool.org/), [c2pa-python](https://pypi.org/project/c2pa-python/), [Chromaprint](https://github.com/acoustid/chromaprint), [deface](https://pypi.org/project/deface/).

Three findings carry caveats that rest only on **secondary sources or inference**, and each has a recommended empirical test:

- **EXIF stripping on compressed photos.** Multiple vendor blogs state photo mode strips EXIF/GPS/XMP while "send as file" preserves original bytes ([fast.io](https://www.fast.io/resources/whatsapp-telegram-signal-photo-metadata-preservation.md); [Viallo](https://www.viallo.app/blog/telegram-photo-compression)). This is structurally consistent with the data model (a photo has no original-bytes rendition; a document is byte-preserved), but no primary, tested OSINT study was found. **Recommended 10-minute test**: send a GPS-tagged, C2PA-signed JPEG and a HEIC both as photo and as file, then diff `exiftool -a -G1 -s` and `c2patool` output. Run ExifTool and c2pa-python only on `messageMediaDocument` images, never photo-mode images (inference).
- **C2PA survival.** Inferred, not tested: photo mode re-encodes and strips the manifest (treat `c2pa:present` on a photo-mode image as impossible); file mode should preserve the hard binding. A valid manifest on a document is an observed fact about claimed origin (signer, AI generator), not proof of who posted it.
- **Video container metadata (MP4 creation_time, GPS `©xyz`)** preservation for videos sent "as video" is undocumented; needs a controlled test.

Perceptual-hash thresholds carry calibration caveats from an independent test (Dalins et al., 225,887 images): format changes matched 99.96% at distance 30, but crops above ~5% and rotations above ~5 degrees push distances past threshold, and random pairs average distance 128 ([arXiv 1912.07745](https://arxiv.org/pdf/1912.07745)). Down-weight high-degree hashes (stock, celebrity, meme, default, GAN-face avatars) with an IDF penalty and store the flag as `tg:hashClass` on the fingerprint so all edges through it inherit the down-weight (inference).

---

## Turtle and Neo4j schema additions (drop-in, with placeholders)

### Turtle: media, annotation, transcript and provenance pattern

```turtle
@prefix tg:      <https://{{YOUR_DOMAIN}}/ontology/tg#> .
@prefix tgr:     <https://{{YOUR_DOMAIN}}/id/> .
@prefix oa:      <http://www.w3.org/ns/oa#> .
@prefix prov:    <http://www.w3.org/ns/prov#> .
@prefix mls:     <http://www.w3.org/ns/mls#> .
@prefix schema:  <https://schema.org/> .
@prefix org:     <http://www.w3.org/ns/org#> .
@prefix time:    <http://www.w3.org/2006/time#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .

# --- A document file (byte-preserved: sha256 identity + metadata valid) ---
tgr:file_{{SHA256}} a tg:File, schema:DigitalDocument ;
    schema:sha256 "{{SHA256_HEX}}" ;
    tg:fileUniqueId "{{FILE_UNIQUE_ID}}" ;
    schema:encodingFormat "application/pdf" ;
    tg:telegramSendMode tg:DocumentMode ;      # DocumentMode => EXIF/C2PA may survive
    schema:contentSize "834112" .

# --- A photo (no original bytes: perceptual fingerprint, not sha256 identity) ---
tgr:photo_{{PHOTO_ID}} a tg:Photo, schema:ImageObject ;
    tg:photoId "{{PHOTO_ID}}" ;
    tg:telegramSendMode tg:PhotoMode ;         # PhotoMode => treat C2PA/EXIF as absent
    tg:hasRendition tgr:photo_{{PHOTO_ID}}_y .
tgr:photo_{{PHOTO_ID}}_y a tg:PhotoRendition ;
    tg:sizeType "y" ; schema:width 1280 ; schema:height 960 ; schema:contentSize "198734" .

# --- Media occurrence in a message (spoiler/TTL live here, not on the file) ---
tgr:att_{{MSG_KEY}}_1 a tg:MediaAttachment ;
    tg:attachmentOf tgr:msg_{{MSG_KEY}} ;
    tg:media tgr:file_{{SHA256}} ;
    tg:spoiler false ; tg:ttlSeconds 0 .

# --- Transcript of a voice note (inferred) ---
tgr:tr_{{UUID}} a tg:Transcript ;
    prov:wasDerivedFrom tgr:file_{{VOICE_SHA256}} ;
    tg:detectedLanguage "uk" ; tg:languageProbability 0.91 ;
    tg:epistemicStatus "inferred" ;
    prov:wasGeneratedBy tgr:act_asr_{{UUID}} .
tgr:seg_{{UUID}}_0003 a tg:TranscriptSegment ;
    dcterms:isPartOf tgr:tr_{{UUID}} ;
    tg:startSec 12.30 ; tg:endSec 17.92 ; tg:text "{{SEGMENT_TEXT}}" ;
    tg:avgLogprob -0.21 ; tg:noSpeechProb 0.02 ; tg:turnLabel "SPEAKER_00" ;
    tg:mediaFragment <https://{{MEDIA_HOST}}/{{VOICE_SHA256}}.ogg#t=12.30,17.92> .

# --- Extraction activity: tool + model + weights, auditable license ---
tgr:act_asr_{{UUID}} a prov:Activity, mls:Run ;
    prov:used tgr:file_{{VOICE_SHA256}} ;
    prov:wasAssociatedWith tgr:agent_fw_turbo ;
    prov:startedAtTime "{{T0}}"^^xsd:dateTime ; prov:endedAtTime "{{T1}}"^^xsd:dateTime .
tgr:agent_fw_turbo a tg:ModelAgent, prov:SoftwareAgent ;
    tg:toolName "faster-whisper" ; tg:toolVersion "1.2.1" ;
    tg:modelId "large-v3-turbo" ; tg:modelRevision "{{HF_COMMIT}}" ;
    tg:weightsSha256 "{{WEIGHTS_SHA256}}" ;
    tg:codeLicense "MIT" ; tg:weightsLicense "MIT" .

# --- OCR identifier as an annotation with a validated checksum ---
tgr:anno_id_1 a oa:Annotation ;
    oa:motivatedBy oa:identifying ;
    oa:hasBody tgr:identifier_tron_{{ADDR}} ;
    oa:hasTarget [ a oa:SpecificResource ;
        oa:hasSource tgr:photo_{{PHOTO_ID}} ;
        oa:hasSelector [ a oa:FragmentSelector ;
            dcterms:conformsTo <http://www.w3.org/TR/media-frags/> ;
            rdf:value "xywh=112,640,820,48" ] ] ;
    tg:validation tg:Base58CheckPassed ; tg:confidence 0.97 ;
    tg:epistemicStatus "inferred" ; prov:wasGeneratedBy tgr:run_idextract .
tgr:identifier_tron_{{ADDR}} a tg:Identifier, tg:CryptoAddress ;
    tg:chain "TRON" ; rdf:value "{{ADDR}}" .

# --- Membership (n-ary, time-bounded, scored, with provenance) ---
tgr:mem_{{UUID}} a tg:GroupMembership, org:Membership ;
    org:member tgr:user_{{USER_ID}} ; org:organization tgr:chat_{{CHAT_ID}} ;
    org:role tg:role_subscriber ;
    org:memberDuring [ a time:Interval ; time:hasBeginning [ time:inXSDDateTime "{{SINCE}}"^^xsd:dateTime ] ] ;
    tg:visibilityScope "admins" ; tg:assertionStatus "observed" ;
    prov:wasGeneratedBy tgr:run_{{COLLECTION_RUN}} .

# --- Identity judgement (nomenklatura model; never owl:sameAs) ---
tgr:judg_{{UUID}} a tg:IdentityJudgement ;
    tg:source tgr:user_{{USER_ID_A}} ; tg:target tgr:PC_{{SHORTUUID}} ;
    tg:judgement "positive" ; tg:score 0.88 ;
    tg:decidedBy "analyst:{{ANALYST_ID}}" ; tg:createdAt "{{TS}}"^^xsd:dateTime ;
    tg:evidence ( tgr:obs_same_personal_channel tgr:obs_avatar_phash_match ) ;
    tg:legalBasis "{{ART9_2J_NATIONAL_SAFEGUARD_REF}}" .
tgr:PC_{{SHORTUUID}} a tg:PersonCluster ; tg:operatedBy tgr:user_{{USER_ID_A}} .
```

### Neo4j label and relationship additions (extends the earlier report's set)

The earlier report defined labels `:Account :Chat :Message :Media :File :Story :StickerSet :Identifier :Person :Observation :Assertion :CollectionRun` and relationships `SENT IN_CHAT REPLIES_TO FORWARD_OF FORWARDED_FROM MENTIONS MEMBER_OF HAS_MEDIA HAS_FILE CONTAINS REACTED CANDIDATE_SAME_PERSON OBSERVED_IN`. Add:

| New label | Purpose |
|---|---|
| `:Community` | group-of-groups container (layer 230) |
| `:Group` (+ `:InferredGroup` / `:RealWorldGroup` / `:CaseGroup`) | all grouping families |
| `:GroupMembership` | reified inferred/analyst membership (plain `MEMBER_OF` kept for observed) |
| `:DetectionRun` / `:GraphSnapshot` | algorithmic provenance |
| `:PhotoRendition` / `:MediaAttachment` | rendition and occurrence model |
| `:Transcript` / `:TranscriptSegment` / `:ExtractedText` / `:DocumentMetadata` | content extraction |
| `:Annotation` / `:MediaFingerprint` / `:Embedding` | CV/OCR outputs and vectors |
| `:IdentityJudgement` / `:PersonCluster` | identity graph |
| `:Device` / `:GeoPoint` / `:Transcript` | metadata and location |

| New relationship | Properties |
|---|---|
| `HAS_RENDITION` (File→PhotoRendition) | sizeType, w, h |
| `HAS_TRANSCRIPT` (VoiceNote/RoundVideo→Transcript) | |
| `HAS_SEGMENT` (Transcript→TranscriptSegment) | startSec, endSec |
| `TARGETS` (Annotation→File/Transcript) | selector (`xywh=` / `t=` / start/end) |
| `HAS_BODY` (Annotation→ExtractedEntity/Identifier) | |
| `FINGERPRINT_OF` (MediaFingerprint→File) | algorithm, hashHex, quality, hashClass |
| `NEAR_DUPLICATE_OF` (File↔File) | metric, distance, threshold, conf, status, method, run |
| `MEMBER_OF_COMMUNITY` (Chat/Channel→Community) | visible, can_view_history |
| `GENERATED_BY` (any derived→DetectionRun/Activity) | model, version, weights_sha256, params_json |
| `GAVE_GIFT_TO`, `REPOSTED`, `BOOSTED`, `FEATURES`, `DELEGATES_TO`, `VERIFIES` | dated, name_hidden where applicable |
| `CANDIDATE_SAME_PERSON` (existing, refined) | judgement, conf, status, method, evidence, decided_by, created_at, deleted_at |

Every derived edge keeps the earlier report's mandatory `conf, status, method, evidence`. Put embeddings on `(:Embedding {vector})` nodes with a vector index (one index per model, Neo4j allows one per label+property), and keep float vectors out of any RDF triple store, with only a pointer plus model metadata in the graph ([Neo4j vector indexes](https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes/)).

---

## A worked example: a group message with a photo-as-file, a voice note and a PDF

Consider one supergroup message collected via MTProto that carries an album of three media: a JPEG sent "as file" (a document, not compressed), a 42-second voice note, and a PDF. This traces raw JSON to extracted entities to graph nodes and edges with provenance.

**Raw (abbreviated MTProto, layer 230):**

```
message#... id:88213 peer_id:peerChannel(channel_id=1600300400) from_id:peerUser(user_id=705502011)
  date:1792550400 grouped_id:1099511627776
  media: messageMediaDocument(document=document(id=512..., mime_type="image/jpeg",
         attributes=[documentAttributeFilename("IMG_8841.JPG"), documentAttributeImageSize(w=4032,h=3024)]))
  (sibling) messageMediaDocument(voice): document(mime_type="audio/ogg",
         attributes=[documentAttributeAudio(voice=true, duration=42, waveform=<bytes>)])
  (sibling) messageMediaDocument: document(mime_type="application/pdf",
         attributes=[documentAttributeFilename("invoice_2026.pdf")])
```

**Step 1, deterministic nodes (observed).** The message keys as `channel:1600300400/msg:88213`; the sender as `user:705502011`; the chat as `channel:1600300400`. Because the three media share `grouped_id`, they attach to one message via three `tg:MediaAttachment` nodes. Each file is downloaded and keyed by sha256.

```cypher
MERGE (m:Message {key:'channel:1600300400/msg:88213'})
MERGE (u:Account {userId:705502011})
MERGE (c:Chat:Supergroup {chatId:1600300400})
MERGE (u)-[:SENT {obs_id:'run:{{RUN}}'}]->(m)
MERGE (m)-[:IN_CHAT]->(c)
MERGE (jpg:File {sha256:'{{SHA_JPG}}'}) SET jpg.mime='image/jpeg', jpg.sendMode='DocumentMode', jpg.fileName='IMG_8841.JPG'
MERGE (ogg:File:VoiceNote {sha256:'{{SHA_OGG}}'}) SET ogg.mime='audio/ogg', ogg.duration=42
MERGE (pdf:File:Document {sha256:'{{SHA_PDF}}'}) SET pdf.mime='application/pdf', pdf.fileName='invoice_2026.pdf'
MERGE (m)-[:HAS_MEDIA {grouped_id:1099511627776}]->(jpg)
MERGE (m)-[:HAS_MEDIA {grouped_id:1099511627776}]->(ogg)
MERGE (m)-[:HAS_MEDIA {grouped_id:1099511627776}]->(pdf)
```

**Step 2, the photo-as-file is the high-value branch (observed + inferred).** Because it was sent as a **document**, the original 4032x3024 bytes are preserved, so ExifTool runs and (if the earlier EXIF test confirms) can yield GPS, device serial and software. Suppose EXIF holds `BodySerialNumber` and GPS. These become `tg:Device` and `tg:GeoPoint` nodes, observed facts about the file, with the person link left as a separate low-confidence inference (cameras are shared or files re-shared).

```cypher
MERGE (dev:Device {bodySerial:'{{SERIAL}}'})
MERGE (jpg)-[:CAPTURED_WITH {status:'observed', run:'exiftool:13.59'}]->(dev)
MERGE (geo:GeoPoint {lat:{{LAT}}, lon:{{LON}}})
MERGE (jpg)-[:HAS_EXIF_GPS {status:'observed', spoofable:true}]->(geo)
```

Had this JPEG been sent "as photo", it would have become a `tg:Photo` with server renditions, no original bytes, no reliable EXIF, and would be fingerprinted with PDQ instead. PDQ runs regardless (on the rendered image) to produce `tg:nearDuplicateOf` edges to the same image reposted elsewhere.

**Step 3, the voice note (inferred, with provenance).** faster-whisper large-v3-turbo transcribes the 42-second Opus file; the waveform bytes decode to ~100 samples for the UI. The transcript and each segment become inferred nodes generated by a logged activity. NER over the transcript text yields an `ExtractedEntity` (say a location) anchored by a `TextPositionSelector`.

```cypher
MERGE (tr:Transcript {id:'{{UUID}}'}) SET tr.lang='uk', tr.langProb=0.91, tr.status='inferred'
MERGE (ogg)-[:HAS_TRANSCRIPT]->(tr)
MERGE (seg:TranscriptSegment {id:'{{UUID}}_0003'}) SET seg.startSec=12.30, seg.endSec=17.92, seg.text='{{TEXT}}'
MERGE (tr)-[:HAS_SEGMENT]->(seg)
MERGE (run:DetectionRun {id:'act_asr_{{UUID}}'}) SET run.model='faster-whisper/large-v3-turbo', run.weightsLicense='MIT'
MERGE (tr)-[:GENERATED_BY]->(run)
```

**Step 4, the PDF (observed + file-declared + inferred).** Apache Tika `/rmeta` extracts text and metadata in a no-network container. `xmpMM:DocumentID` and OOXML/PDF `Author`/`Producer` become `tg:DocumentMetadata` marked **file-declared** (forgeable, never auto-merged into a Person). Checksum-validated identifiers in the text (an IBAN, a crypto wallet) mint `tg:Identifier` nodes; a shared `xmpMM:DocumentID` across channels links re-saved renditions via `tg:sameDocumentLineage` even when sha256 differs.

```cypher
MERGE (meta:DocumentMetadata {xmpDocumentID:'{{XMP_DOC_ID}}'})
  SET meta.author='{{AUTHOR_STRING}}', meta.producer='{{PRODUCER}}', meta.status='file-declared'
MERGE (pdf)-[:HAS_METADATA]->(meta)
MERGE (iban:Identifier:IBAN {value:'{{IBAN}}'})
MERGE (pdf)-[:CONTAINS_IDENTIFIER {status:'inferred', validation:'iban-checksum', conf:0.96}]->(iban)
```

**Step 5, the payoff.** The device serial from the JPEG, the location named in the voice note, and the IBAN in the PDF are now queryable alongside the sender and chat. If another account in a different channel posts a file with the **same body serial** or the **same IBAN**, TGO raises a scored `tg:IdentityJudgement` (evidence = shared device, shared identifier) that an analyst confirms or rejects; it is never an automatic `owl:sameAs`. Every node carries its collection run, every derived node its model and license, so the whole chain is auditable and reversible (`explode` on erasure request).

---

## Visualization favors open WebGL, with Microsoft-stack complements

No open-source tool gives a complete Telegram investigation UI, so the recommended approach is a small set of composable, permissively licensed pieces. For the **graph itself**, the only stacks that are OSI-licensed (no CC-BY-NC, SSPL or BSL), actively maintained in October 2026, WebGL, and able to show more than ~10k elements in a browser are **sigma.js v4 + graphology (MIT)** and **cosmos.gl (MIT, OpenJS incubating)**, with **Gephi 0.11 (GPL-3.0)** as the desktop analysis and timeline anchor.

### Graph rendering libraries

| Tool | License | Rendering | Documented scale | Time / combos / confidence encoding |
|---|---|---|---|---|
| Gephi 0.11.3 | GPL-3.0 / CDDL | OpenGL desktop | "up to a million elements" | timeline over GEXF intervals/timestamps (pick one per file); partition, no combos |
| sigma.js v4 + graphology | MIT | WebGL2 | claims large graphs (no number) | `layerDashed` = inferred, `opacity` = confidence, image/border/pie nodes; GPU FA2; time via visibility styles (DIY) |
| cosmos.gl 3.5 | MIT (OpenJS) | GPU force sim | "hundreds of thousands of points and links" | dashed/dotted `linkDefaultStyle`, `linkOpacity`, cluster force; time DIY |
| Cytoscape.js 3.34 | MIT | Canvas (WebGL preview) | 3.2k n / 68k e ~10 fps (WebGL) | best compound nodes; WebGL preview cannot draw dashed edges |
| AntV G6 v5 | MIT | Canvas default | G6 4.8.7: 1k nodes >30 fps | richest built-ins: `timebar`, combos, `hull`, `bubble-sets`, edge bundling |
| Reagraph 4.32 | Apache-2.0 | WebGL (three.js) | not documented | clustering, edge bundling, 2D/3D |
| Cosmograph 2.5 | **CC-BY-NC-4.0, closed** | WebGL + DuckDB | "millions" | timeline; avoid for anything commercial (use `@cosmos.gl/graph` directly) |
| ipysigma 0.24 | MIT | sigma in Jupyter | not documented | analyst notebook viewer |
| Graphistry 0.60 | BSD client / commercial GPU server | WebGL client | "millions of edges" | do not use free Hub for PII |

Sources: [sigma CHANGELOG](https://github.com/jacomyal/sigma.js/blob/HEAD/CHANGELOG.md), [cosmos.gl](https://github.com/cosmosgl/graph), [Cytoscape.js WebGL blog](https://blog.js.cytoscape.org/2025/01/13/webgl-preview/), [G6 plugins](https://github.com/antvis/G6/tree/HEAD/packages/g6/src/plugins), [Gephi docs](https://github.com/gephi/gephi-documentation/blob/HEAD/gephi-desktop/docs/User_Manual/Import_Dynamic_Data.md), [Cosmograph npm](https://www.npmjs.com/package/@cosmograph/cosmograph). The best-evidenced **uncertainty encoding** (Guo, Huang & Laidlaw, IEEE TVCG 2015) found transparency, grain and fuzziness robust for edge certainty, and lightness accurate except next to hue ([paper](https://jeffhuang.com/papers/EdgeUncertainty_TVCG15.pdf)). TGO's recommended mapping: **edge hue** = relation type, **opacity/lightness (3-4 bins)** = confidence, **solid vs dashed** = observed vs inferred, **width** = event count, **node image** = avatar (blurred faces, below), with a **time-range filter** and **confidence slider**, plus a two-layer toggle between an account graph and a person-cluster graph (replacing the combos sigma lacks). No open tool ships an "explain this edge" provenance panel, so build it as a `clickEdge` handler calling `GET /edges/{{id}}/evidence` that returns the messages, timestamps, rule/model, score and features (inference).

### Non-graph panels (media, timeline, map, audio, search)

| Need | Open-source pick | License | Why |
|---|---|---|---|
| Media / embedding explorer | FiftyOne 1.22 | Apache-2.0 | CLIP embeddings, lasso, near/exact dups, Map panel; "analyst workbench" |
| Embedding component in custom UI | Apple Embedding Atlas 0.26 | MIT | WebGPU, Mosaic/DuckDB, cross-filtered views, npm components |
| Media browser (optional) | Immich 3.3 | AGPL-3.0 | CLIP/OCR search, map, duplicates; **keep face clustering OFF** (below) |
| Timeline (ranges) | vis-timeline 8.5 | Apache-2.0 OR MIT | live-location periods, membership spells |
| Posting heatmap | ECharts 6.1 | Apache-2.0 | hour x weekday, calendar, brush, dataZoom in one lib (synchronized-posting view) |
| Cross-filter glue | Mosaic 0.33 + DuckDB-WASM | BSD-3 / MIT | brushing-and-linking bus for millions of rows |
| Map | deck.gl 9.4 + MapLibre 6.14 | MIT / BSD-3 | H3 bins, MVT, TripsLayer for live locations |
| Map (quick exploration) | kepler.gl 3.2 | MIT (OpenJS) | drag-and-drop, DuckDB SQL, time playback |
| Audio | wavesurfer.js 8 (+ whisperX regions) | BSD-3 | decode Telegram 5-bit waveform to instant peaks; transcript-synced regions |
| Search | Meilisearch (MIT core) or Typesense (GPL-3.0) | MIT AND BUSL / GPL-3.0 | typo-tolerant, faceted, geo, hybrid |

Sources: [FiftyOne](https://github.com/voxel51/fiftyone), [Embedding Atlas](https://github.com/apple/embedding-atlas), [Immich](https://github.com/immich-app/immich), [vis-timeline](https://github.com/visjs/vis-timeline), [ECharts](https://github.com/apache/echarts), [Mosaic](https://github.com/uwdata/mosaic), [deck.gl](https://github.com/visgl/deck.gl), [kepler.gl](https://github.com/keplergl/kepler.gl), [wavesurfer.js](https://github.com/katspaugh/wavesurfer.js), [Meilisearch](https://github.com/meilisearch/meilisearch), [Typesense](https://github.com/typesense/typesense). The Telegram voice waveform decodes with a short JS port of the tdesktop logic (`valuesCount = bytes*8/5`, each 5-bit value `(uint16_le >> bitShift) & 0x1F`, ~100 samples normalized to 0-1), so voice lists render as waveform thumbnails before any audio is downloaded ([tdesktop data_document.cpp](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/data/data_document.cpp)). Immich's map will be mostly empty for Telegram photos (compressed mode strips GPS), and its face clustering (DBSCAN-derived) must be disabled (below).

### Microsoft-stack options (the lead works heavily in Microsoft)

| Option | Status | Fit |
|---|---|---|
| **Graph in Microsoft Fabric** | reported GA June 2026 (secondary source: [Directions on Microsoft](https://www.directionsonmicrosoft.com/roadmaps/ref/fabric-iq-roadmap/)); Neo4j Graph Intelligence for Fabric reported GA 2025-10-23 | GQL (ISO/IEC 39075) over OneLake tables; built-in diagram/card/table views; ~2B elements, 64 MB result cap, no continuation token, no direct Power BI graph visual, no result export, so it needs its own front end (sigma.js) hitting the GQL API, sized to ego-network or community fetches ([Fabric limitations](https://learn.microsoft.com/en-us/fabric/graph/limitations)) |
| Fabric Real-Time Dashboards | GA | KQL tiles: map, heatmap (hour x weekday), time chart, cross-filter; a credible "monitoring" view in hours |
| Power BI Azure Maps + MIT force-graph visual | proprietary + MIT visual | slide-ready views; `powerbi-visuals-forcegraph`, `-Timeline`, `-heatmap`, `-sankey` are MIT ([forcegraph](https://github.com/microsoft/powerbi-visuals-forcegraph)) |

Neither Fabric nor Power BI offers media galleries or audio playback, so they complement the open-source UI rather than replace it. NVL (Neo4j's own visualization library) is licensed only for Neo4j products, so it is a lock-in point to avoid if Fabric is the future store.

### Interoperability exports

Export TGO to **FtM** (account → `UserAccount`, channel → `Organization`/`Membership` target, message → `Message`, wallet → `CryptoWallet`, candidate-same-person → `Similar` with `confidenceScore`) for OpenAleph (MIT, the active Aleph fork), and to **STIX 2.1** (Channel, User-Account, Cryptocurrency-Wallet, Url, Phone-Number) via `pycti` for OpenCTI CE (Apache-2.0), which already models a STIX `Channel` entity ([OpenCTI](https://github.com/OpenCTI-Platform/opencti); [OpenAleph](https://github.com/openaleph/openaleph); [FtM Similar](https://followthemoney.tech/explorer/schemata/Interest/)). Neither platform scales visually past small curated subgraphs; they are investigation workbenches, not overview visualizers.

---

## Biometrics, leaked data and the sticker heuristic are deliberately excluded

Three capabilities are **out of scope by design**, not by omission, and the ontology must not carry edges that depend on them.

**No biometric identification of people.** No face recognition or face clustering to link accounts, and no speaker or voice matching between accounts. Face embeddings processed for unique identification are GDPR **Art. 4(14)/Art. 9 biometric data** ([gdpr-info Art. 9](https://gdpr-info.eu/art-9-gdpr/)), and building a searchable face index from untargeted scraping of avatars can meet all four cumulative conditions of **EU AI Act Art. 5(1)(e)**, whose Commission guidelines say a database is covered if it "can be used for facial recognition" and that clustering to link accounts of the same person is "recognition" ([FPF analysis](https://fpf.org/blog/red-lines-under-the-eu-ai-act-understanding-the-ban-of-the-untargeted-scraping-of-facial-images-and-facial-recognition-databases/)). The Clearview enforcement record (Netherlands EUR 30.5M, 2024; France and Italy EUR 20M each) and Datatilsynet's 2020 position that Clearview is "probably not lawful" confirm the exposure in the EEA regardless of the AI Act's Norwegian timeline ([Hunton](https://www.hunton.com/privacy-and-cybersecurity-law-blog/dutch-regulator-fines-clearview-ai-30-5-million-euros); [ABC Nyheter 2020](https://www.abcnyheter.no/nyheter/det-gar-mot-forbud-for-politiets-bruk-av-clearviews-ansiktsgjenkjenning/433683)). The parallel ban on inferring race, religion, political views or orientation from faces (Art. 5(1)(g)) rules out DeepFace race/emotion modules and speaker-demographic labels. **Recommended non-biometric alternatives**: whole-image perceptual hashing of avatars (PDQ/pHash) to find the same picture reused across accounts, which identifies the image and not the face and so is not biometric processing under GDPR Recital 51 (inference; a DPA could still view systematic linking as profiling under Art. 6), with IDF down-weighting of stock/celebrity/meme/default/GAN-face avatars; and **face detection used only to blur faces in the UI** (deface or EgoBlur, or YuNet plus Gaussian blur). Where a visualization tool such as **Immich** ships face clustering (DBSCAN-derived), **keep that feature off** and use only its CLIP search, OCR and duplicate panels ([Immich facial-recognition docs](https://github.com/immich-app/immich/blob/HEAD/docs/docs/features/facial-recognition.md)). Diarization, if used for transcript turn labels, stays per-recording and anonymous (`SPEAKER_00`), with no embedding storage and no cross-recording matching, to stay outside biometric scope.

**No leaked databases or stolen credential logs.** TGO ingests only Telegram data obtained through the collection runs it records; it does not incorporate breach dumps, combolists or stolen credential logs, and password-protected archives are recorded as `tg:isEncrypted true` and left closed (trying passwords found in a chat is access-circumvention and out of scope) ([Python zipfile docs](https://docs.python.org/3/library/zipfile.html)).

**No sticker-set-ID creator heuristic.** The earlier report listed "shared sticker-pack authorship" via a set-id heuristic as a person link at 0.6 confidence; **this report drops it**. The claim that a sticker or custom-emoji pack ID encodes the creator's numeric user ID comes only from unofficial write-ups that neither test reliability across pack types and ages nor report a Telegram response, the official API documents no ID-based ownership heuristic, and the technique is a direct de-anonymization of possibly vulnerable people ([HackerNoon](https://hackernoon.com/whats-wrong-with-stickers-in-telegram-deanonymize-anonymous-channels-in-two-clicks); [core: stickers API](https://core.telegram.org/api/stickers)). TGO represents sticker-set ownership only as (a) `tg:ownedBy` when the collecting account is the owner (the `creator` flag) or the creating bot (the `_by_<bot>` short-name suffix), both observed facts, and never as an inferred creator from the set ID. Sticker and custom-emoji **usage** remains a legitimate behavioural facet: a per-account TF-IDF vector over sticker-set and custom-emoji usage, where a shared rare or private set is a weak-to-moderate co-membership signal, with the IDF statistics logged in the provenance (inference).

Because Telegram's own ToS bans scraping for datasets and AI, and the EEA joint statement signed by Datatilsynet holds that "organisations must have a lawful reason for collecting and using people's data, even when it is publicly available", group-membership edges to religious, political, health, trade-union or sexuality-linked chats are treated as **Art. 9 data by default** (the edge, not just a profile field, is the special-category datum, per CJEU C-184/20 and the Grindr ruling), gated behind a documented Art. 9(2)(j) research basis with national safeguards, a DPIA, role-based access and short retention ([ICO joint statement](https://cy.ico.org.uk/about-the-ico/media-centre/news-and-blogs/2023/08/joint-statement-on-data-scraping-and-data-protection); [FRA on C-252/21](https://fra.europa.eu/en/node/44533)).

---

## A ranked 48-hour hackathon build plan

The ordering maximizes linking value per hour: build the identity and hashing backbone first, because every later extractor attaches to `tg:File` and `tg:MediaFingerprint`.

| Rank | Build step | Effort (est.) | Yield |
|---|---|---|---|
| 1 | Ingest Telethon JSON/JSONL and Telegram Desktop `result.json` into TGO (L1 Core + L2 Content); deterministic keys; sha256 + `file_unique_id` on every file | 3-4 h | the graph exists; forward/reuse chains via `sameFileAs` |
| 2 | PDQ over all images/avatars/sticker first frames/video keyframes into Faiss `IndexBinaryFlat`; `nearDuplicateOf` edges with IDF-weighted avatar linking | 1-2 h | cross-channel reposts and shared avatars (very high linking value) |
| 3 | CLIP/SigLIP pass as router (screenshot/meme/photo/document) and index in Qdrant | 1-2 h | triage + clustering |
| 4 | Gated OCR (PP-OCRv5 on screenshot-classified images) + checksum-validated identifier extraction | 2-3 h | `tg:Identifier` nodes (wallets, phones, URLs, handles) |
| 5 | Apache Tika `/rmeta` on documents (no-network container) + faster-whisper on voice notes/round videos + NER | 4-6 h | document text/metadata, transcripts, extracted entities |
| 6 | GDS Community (Louvain/Leiden/WCC) or graphology Louvain for communities and operator clusters; CooRTweet-style co-forward/co-post detection | 2-3 h | `tg:CommunityCluster`, `tg:CoordinationCluster` with run provenance |
| 7 | Web UI: React + sigma.js v4 (dashed=inferred, opacity=confidence, avatar nodes with blurred faces), time-range + confidence sliders, account/person layer toggle, click-edge evidence panel | 6-8 h | the demo centerpiece |
| 8 | Gephi 0.11 dynamic GEXF (intervals) for a recorded timeline video; WebVOWL + pyLODE + pySHACL for the ontology | 1-2 h | offline backup + ontology rigor |
| 9 | ExifTool + c2pa on document-mode files; sticker/story media-area metadata (zero CV) | 1-2 h | device/GPS hits, observed geo/URL/channel edges |
| 10 | FtM + STIX exports | 1-2 h | interoperability slide (OpenAleph, OpenCTI) |

Skip for the hackathon: face recognition (excluded), geolocation models (GeoCLIP is only ~14% within 1 km on IM2GPS3K, and most Telegram images are indoor/screenshots/memes, out of distribution), and deepfake detectors (open detectors drop 45-50% AUC on in-the-wild 2024 data), storing any of these only as weak, clearly-labeled `inferred` hints if at all ([G3 paper](https://arxiv.org/html/2405.14702v2); [Deepfake-Eval-2024](https://arxiv.org/html/2503.02857v4)). Use a VLM (Qwen3-VL, Apache-2.0) only on a small gated subset of chat screenshots, cross-checking its identifiers against OCR spans to catch hallucinations. On a CPU-only laptop, run OCR and ASR on a prioritized subset (channels of interest, speech-classified audio over 10 s). The longer-term product keeps sigma.js for ego/community views, adds cosmos.gl for million-scale overviews, puts a store-agnostic graph API in front of Neo4j or Fabric Graph with server-side aggregation, and keeps the FtM/STIX exports.

---

## What is not obtainable

Some facts the project will want simply cannot be collected, and TGO should represent their absence explicitly rather than guess.

| Not obtainable | Why |
|---|---|
| Original uploaded bytes of a compressed photo | the API exposes only server-resized renditions; no original rendition exists ([core: files](https://core.telegram.org/api/files)) |
| Reliable EXIF/GPS/C2PA from photo-mode images | re-encoded JPEGs; survives only on documents (secondary source, test recommended) |
| Transcripts, waveforms via Bot API or Desktop export | only MTProto/TDLib `messages.transcribeAudio` populates them; Bot API and export never do ([core: transcribe](https://core.telegram.org/api/transcribe)) |
| Channel subscriber lists, boosters, join requesters, story viewers, monoforum senders | admin/owner only; invisible to a non-admin collector |
| Member lists via the Bot API | bots cannot enumerate members; they see people only through updates and `getChatMember` |
| Other users' privacy rules | only the collector's own `account.getPrivacy` returns rules; others show only effects |
| A verified sticker-set creator from the set ID | unofficial, untested, and excluded by design |
| Message-media IDs in the Desktop export | export JSON carries no document/photo IDs for message media; join by (chat, msg, date) or sha256 |
| People Nearby geo-groups | the feature was retired September 2024, though `contacts.getLocated` lingers in the schema ([such.chat](https://www.such.chat/blog/telegram-people-nearby-what-happened)) |
| Street-level geolocation from open models | GeoCLIP/G3 give country-to-region guesses, not evidence; biometric and bulk geolocation of private individuals is excluded |

The deeper point is that **absence is itself data in TGO**: a `tg:FieldVisibility` of `hidden_by_privacy`, a `tg:fileStatus` of `excluded_by_size`, or a membership that is only `owner_only`-visible each tells the analyst something the raw record does not. Modeling the unobtainable as typed gaps, rather than silently empty fields, is what lets TGO stay honest about the difference between "this person has no bio" and "this person's bio is hidden from this collector", and it is the same discipline that keeps every person link a reversible, evidenced judgement rather than an assertion of identity. That discipline, more than any single extractor, is what makes a hackathon graph defensible enough to become a product.
