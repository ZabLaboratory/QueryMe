# ADR 001 — Positionnement et architecture de QueryMe

- **Status**: accepted
- **Date**: 2026-08-12
- **Decided**: 2026-08-15
- **Deciders**: @ClodoCapeo
- **Author**: Atlas
- **Supersedes**: —
- **Superseded by**: —

## 1. Context

Le porteur veut créer QueryMe, décrit initialement comme « un standard générique d'interface
DB, façon GraphQL universel », décliné en trois couches : QueryMe (OSS générique),
QueryMe-Blue (package exposé *par* QueryMe *pour* Blue) et QueryMe-Zapp (déclinaison privée
portant la doctrine de confiance Zab, avec gate à la production d'une Zapp et devkit DB pour
les auteurs).

Une thèse de positionnement est déjà validée : la plus-value OSS n'est pas le chiffrement en
transit (TLS/mTLS est table stakes chez tous les concurrents) mais une garantie structurelle
par construction — « l'accès DB qui ne peut pas être mal configuré », pas « l'accès DB
chiffré ».

Le dépôt `ZabLaboratory/QueryMe` **existe déjà**, public depuis sa création le 2026-04-27,
dernier push le 2026-06-20. Il porte une bibliothèque Python pure, sans service ni
entrées-sorties, décrite comme l'outillage de requête partagé de la plateforme : un
`QueryDescriptor` déclaratif sans SQL brut, un `SchemaDescriptor` que chaque service retourne
sur son endpoint `_schema`, une validation par liste blanche `validate_against_schema()`, et un
`compile_query()` resté à l'état d'ébauche. **Six services l'utilisent en production** — Blue,
ZabTruth, ZabRanking, ZabAuth, ZabCam, ZabCanvas — tous épinglés sur le tag `v0.2.2`.

Le présent ADR n'ouvre donc pas un projet neuf : il décide **l'évolution d'un projet vivant, déjà
en production**, vers un standard de conformité et d'attestation. La distinction n'est pas de
forme. Le scaffold existant place la vérité du côté du service, chacun décrivant sa forme à
l'exécution, et la validation du côté du client, qui peut ne pas l'appeler. L'ADR renverse les
deux : la surface devient déclarée, compilée et attestée en amont, et la garantie cesse d'être
une bibliothèque qu'on invoque pour devenir une condition d'exécution. Le v0.2.2 illustre, à
petite échelle et sans faute de sa part, le régime consultatif que le constat Supabase ci-dessous
rend insuffisant.

Ce scaffold n'est adossé à aucune décision écrite : le `CLAUDE.md` du dépôt renvoie à un ADR
`001-blueprint-db-access` introuvable, ni dans le dépôt ni ailleurs dans l'organisation. Le
rationnel de la ligne `0.x` n'est donc consigné nulle part, et le présent document est la
**première décision écrite gouvernant ce dépôt**. Rien d'antérieur n'est à réconcilier ; rien
d'antérieur ne peut non plus être invoqué pour justifier l'existant.

L'ADR décide en conséquence : le positionnement, la frontière entre couches, le niveau
d'opération du gate, le régime des opérations non bornées, le régime de débit par identité, le
régime d'accès avant validation, la nature du runtime de référence, le devenir de l'existant, la
trajectoire de version qui protège ses consommateurs, le retrait du chemin legacy dont dépend la
revendication, la structure de dépôts et le domicile de ce document.

État du marché constaté (2026, sources vérifiées) :

- PostgREST : Postgres uniquement, REST, sécurité déléguée aux rôles Postgres et à RLS.
  CVE-2022-35912 a permis une escalade superuser via `db-pre-request`, contournant
  intégralement RLS.
- Hasura : moteur v3 en Rust sous Apache-2.0, connecteurs NDC entièrement OSS, CLI / console /
  LSP propriétaires ; l'entreprise a pivoté vers PromptQL (agentique).
- PostGraphile : Postgres uniquement, GraphQL, modèle de sécurité RLS.
- Supabase : Postgres + PostgREST ; RLS deny-by-default, linter Splinter, Security Advisor,
  alertes mail sur table sans RLS, bascule des grants automatiques vers l'opt-in. La correction
  des règles d'accès *est* la totalité du modèle de sécurité.
- GraphQL Mesh v1 + Hive Gateway v2 (MIT) : composition de REST/OpenAPI/SOAP/gRPC et de bases
  de données en un supergraph compatible Apollo Federation.
- Cube (Apache-2.0) : couche sémantique, métriques gouvernées exposées en SQL, REST, GraphQL
  et MCP.

Deux faits de terrain cadrent la décision. Premièrement, sur six mois de tests d'applications
Supabase, le finding critique le plus fréquent est une policy RLS exposant toutes les lignes à
tout utilisateur authentifié : le problème du secteur n'est pas l'absence d'outil de contrôle,
c'est que les contrôles existants sont *consultatifs*. Deuxièmement, l'accès machine aux bases
explose (SDK MCP à ~97 M de téléchargements mensuels en mars 2026, plus de 10 000 serveurs
publics indexés) alors que 29 % seulement des organisations se déclarent prêtes à sécuriser
l'agentique, et que les garde-fous au niveau du modèle sont établis comme insuffisants face à
l'injection de prompt. La réponse qui converge chez les fournisseurs (AlloyDB MCP GA, MCP
managé CockroachDB) est l'application de garde-fous *sous* l'agent, indépendamment du rôle SQL
sous-jacent.

Les consommateurs visés par les couches 2 et 3 — des règles Blue et des Zapps — sont
précisément des consommateurs machine. Le besoin Zab n'est pas une contrainte plaquée sur une
histoire OSS : c'est l'instance canonique du problème général. Un agent compromis est un
appelant **légitimement authentifié** : il ne franchit aucun contrôle d'identité, il abuse d'un
accès qu'il détient, et son chemin le plus large n'est pas l'opération coûteuse mais la
**pagination répétée d'une opération parfaitement nominale**. C'est ce qui rend le régime de
débit (§3.4) central et non accessoire.

## 2. Decision drivers

1. Défendabilité : ne pas engager une course de rattrapage contre un fédérateur de connecteurs
   financé et déjà en production.
2. Falsifiabilité : une revendication de sécurité doit être mécaniquement vérifiable et
   reproductible par un tiers, sinon elle est une case cochée de plus.
3. Absence de fenêtre d'exposition entre la création d'une Zapp et sa validation.
4. Expérience auteur : un gate qui bloque l'itération ne sera pas adopté et sera contourné.
5. Indépendance du standard vis-à-vis de Zab : QueryMe reste maître de sa surface, Blue et les
   Zapps sont des consommateurs gatés parmi d'autres.
6. Dette de synchronisation nulle entre le public et le privé.
7. Honnêteté du périmètre de garantie : ce qui n'est pas garanti doit être écrit.
8. Souveraineté du chemin d'exécution : la validité d'une attestation ne doit pas dépendre du
   comportement d'un composant tiers versionné hors du projet.
9. Permissivité graduée : une opération coûteuse légitime doit être réalisable. Une interdiction
   structurelle sans voie de sortie est contournée hors du produit, ce qui détruit la garantie
   au lieu de la renforcer.
10. Résistance à l'appelant légitime mais compromis : un contrôle qui n'agit qu'à l'appel
    unitaire ne borne rien face à un adversaire qui dispose du temps et de la concurrence.
11. Continuité des consommateurs : six services dépendent de l'existant en production. Aucune
    décision de cet ADR ne doit modifier ce qu'un consommateur reçoit sous son épinglage actuel,
    ni le contraindre à migrer avant que sa cible existe.

## 3. Decision

### 3.1 Positionnement — descope du « gateway universel »

QueryMe **n'est pas** une passerelle d'accès aux données de plus. L'artefact que QueryMe produit
n'est pas une surface de requête : c'est **un verdict sur une surface de requête**.

QueryMe est défini comme **un standard de conformité et d'attestation pour les surfaces d'accès
aux données, accompagné d'une implémentation de référence**.

Le caractère multi-format est **une conséquence, pas le produit** : le modèle de conformité
s'exprime sur une description normalisée et source-agnostique de la surface, et les adaptateurs
n'existent que pour produire cette description et compiler vers la surface native
correspondante. La construction d'un écosystème de connecteurs concurrent de NDC ou de Hive
Gateway est explicitement **hors périmètre**.

Le fossé concurrentiel visé est un fossé d'incitation, non de technique : un éditeur dont la
promesse commerciale est « votre application tourne en cinq minutes » ne peut pas livrer un
composant dont la fonction est de refuser de servir une application. Le refus est
structurellement anti-onboarding. C'est ce qui rend la position tenable face à des acteurs mieux
financés, et ce qui la rend fragile si QueryMe dérive vers la commodité (§5 R1).

### 3.2 Niveau d'opération du gate

Le gate est **statique, exercé sur un manifeste normatif de surface**, appliqué à la publication
*et* revérifié au démarrage du runtime.

Le principe qui rend « prouvé, pas déclaré » réalisable : on ne prouve pas de propriétés sur du
SQL arbitraire — **on restreint la surface exprimable à un fragment décidable**. L'objet de
conformité est un manifeste déclaratif, typé et source-agnostique, et le runtime n'a le droit de
servir *que* ce qui en dérive. Ce qui n'est pas dérivable du manifeste n'est pas « refusé par une
policy » : c'est **absent de la surface exécutable**. C'est la différence entre le deny-by-default
(où la correction de vos policies reste le modèle de sécurité) et le **non-exprimable par
construction**.

La décidabilité vient d'un choix structurant : **QueryMe génère la surface SQL, il ne l'analyse
pas.** Vérifier qu'une policy RLS écrite à la main assure un cloisonnement total est indécidable
en général ; vérifier qu'un SQL généré à partir d'un manifeste le fait est une propriété du
générateur, établie une fois.

**Trois régimes d'invariants**, distingués par ce qu'il advient d'une violation. La distinction
est normative, et elle est aussi une distinction de **force de garantie** :

- **Régime structurel** (I1, I2, I4, I5, I6, I8) — ce qui viole est **non exprimable** : absent
  de la surface exécutable, sans voie de sortie ni dérogation. La garantie ne dépend d'aucun
  composant à l'exécution.
- **Régime budgétaire** (I3, I7) — ce qui dépasse est exprimable et compilable, mais **non
  exécutable sans autorisation budgétaire signée** (§3.3). La garantie n'est plus l'impossibilité
  mais l'explicitation : aucune opération non bornée ne s'exécute sans décision tracée,
  attribuable et révocable.
- **Régime de débit** (I9) — ce qui dépasse est exprimable *et* exécutable, et le dépassement est
  **empêché par application**, non par impossibilité (§3.4). La garantie y repose sur la
  correction du comptage, sur sa disponibilité et sur le caractère conservateur de ses
  approximations — c'est une garantie **appliquée**, et elle est nommée comme telle partout où
  elle est revendiquée.

**Propriété commune aux trois régimes : l'obligation de *déclarer* est toujours structurelle.**
Un manifeste muet sur le cloisonnement, sur la borne d'une opération de liste ou sur l'une ou
l'autre des deux limites de débit d'une relation **ne compile pas**. Seule la conséquence d'un *dépassement* diffère.

La revendication publique se formule donc en trois temps, dont le troisième porte explicitement
sa nature : *aucune surface ne peut être mal configurée* — impossibilité ; *aucune opération non
bornée ne s'exécute sans autorisation explicite* — décision tracée ; *aucune identité n'extrait ni
ne modifie au-delà des débits déclarés* — **application**, garantie par un mécanisme d'exécution et non par
une impossibilité d'expression. Présenter le troisième temps comme les deux premiers serait de la
survente (R2).

- **I1 — Tenancy totale.** *(structurel)* Toute relation atteignable porte un prédicat de
  cloisonnement sur un discriminant déclaré, dérivé de l'identité appelante et jamais d'un
  paramètre de requête. Vérification : atteignabilité sur le graphe du manifeste, aucune relation
  sans liaison.
- **I2 — Aucune autorité ambiante.** *(structurel)* L'identité effective dérive toujours d'un
  justificatif vérifié ; aucun rôle anonyme ne détient de grant. Vérification : énumération
  rôles × relations atteignables.
- **I3 — Effets bornés ou budgétés.** *(budgétaire)* Le régime s'apprécie **par axe** (§3.4) : sur
  l'axe d'**extraction** pour ce qu'une opération restitue, sur l'axe de **mutation** pour ce
  qu'elle modifie. Sur chaque axe, une opération est soit **bornée** — borne supérieure dérivable à
  la compilation, et pagination par curseur en extraction ; régime nominal, sans friction — soit
  **budgétée** : elle compile, et son exécution exige une autorisation portant un plafond sur cet
  axe (§3.3). Une opération peut être bornée sur un axe et budgétée sur l'autre : une écriture de
  masse est typiquement bornée en extraction, ne restituant rien, et budgétée en mutation. Aucune
  opération n'est rejetée à la compilation pour ce seul motif.

  *Nuance, assumée et normative :* la permissivité est **par opération**, elle n'est pas illimitée
  à l'échelle de la surface. Un profil déclare une proportion maximale d'opérations budgétées par
  manifeste (§3.3, §3.5) ; un manifeste qui la dépasse est refusé **en tant que manifeste**. Sans
  ce garde-fou, le régime budgétaire dégénérerait en tampon et le gate redeviendrait un label
  (R11).
- **I4 — Surface de champs fermée.** *(structurel)* Les champs sont opt-in ; une colonne nouvelle
  est inatteignable tant qu'elle n'est pas exposée explicitement. La projection déclarée par
  opération est aussi ce qui rend calculable le graphe d'exposition du §3.4.
- **I5 — Pas d'échappement d'expression.** *(structurel)* Aucun passage de SQL ou de filtre libre ;
  les prédicats proviennent d'un ensemble fermé d'opérateurs sur des champs déclarés.
- **I6 — Écritures typées par intention.** *(structurel)* Les écritures sont des opérations nommées
  à pré/post-conditions déclarées, pas des mutations de table. Aucune capacité DML directe n'est
  accordée au rôle applicatif. Les relations qu'une opération déclare modifier sont aussi ce qui
  rend calculable le graphe de mutation du §3.4 — symétrique du graphe d'exposition que la
  projection d'I4 rend calculable.
- **I7 — Coût déterministe ou budgété.** *(budgétaire)* Sur chacun de ses deux axes, une opération
  porte une borne de coût statique ou relève du régime budgété de I3. Le coût demeure déterministe
  *une fois l'autorisation émise* : les plafonds des deux axes sont connus avant exécution.
- **I8 — Auditabilité.** *(structurel)* Toute opération servie est attribuable à un nœud du
  manifeste et à une identité appelante, de façon prouvable par les journaux. Toute exécution
  budgétée ou imputée à un débit est en outre attribuable à son autorisation ou à son allocation.
  Réalisé par le répartiteur (§3.8).
- **I9 — Débit borné.** *(débit)* Toute relation atteignable porte **deux limites de débit
  déclarées** — un coût cumulé maximal par identité et par fenêtre en **extraction**, et un second
  en **mutation**. Les deux axes sont distincts parce que les risques le sont : une relation peut
  légitimement tolérer une forte cadence de lecture et une cadence d'écriture quasi nulle, et un
  seuil unique obligerait à calibrer sur le plus permissif des deux en perdant la protection sur
  l'autre. Un manifeste comportant une relation atteignable sans ces deux limites **ne compile
  pas** : c'est la part structurelle de I9.
  Le dépassement, lui, est **empêché à l'exécution** par réservation et comptage (§3.4), non rendu
  inexprimable. Là où I1 borne *qui* voit *quoi*, I9 borne *combien* une identité extrait et modifie dans le
  temps.

  *L'obligation porte sur toute relation atteignable, alors que seules les relations exposées sont
  imputées (§3.4).* Ce n'est pas une inadvertance : l'exposition est une propriété du couple
  (opération, relation), non de la relation — une relation traversée par une opération est exposée
  par une autre. Conditionner l'obligation de déclarer à l'exposition la briserait au premier ajout
  d'une opération exposante, et la briserait silencieusement. Une relation qu'aucune opération
  n'expose porte donc une limite jamais consommée : sans effet, et bien moins coûteux qu'une règle
  conditionnelle fragile.

Trois artefacts signés, distincts et jamais fusionnés, portent le régime :

- **L'attestation de conformité** porte sur le hachage du tuple (manifeste, **artefact généré**,
  profil — identifiant et version —, version d'adaptateur, version du jeu d'invariants, version de
  l'évaluateur). L'artefact appartient au tuple : le runtime ne détient que l'artefact et
  l'attestation, et doit pouvoir établir leur correspondance **sans inspecter l'artefact** (RC-7).
  Le profil y appartient également : c'est ce qui rend une attestation `dev` structurellement
  inutilisable en `prod` (§3.6). L'attestation est une **fonction pure** de ces entrées : un tiers
  ré-exécute la passe attestante et obtient la même charge utile, octet pour octet.
- **Les autorisations budgétaires** (§3.3) portent sur des opérations individuelles.
- **Les allocations de débit** (§3.4) portent sur des quadruplets (identité, sous-clé, relation, axe).

Ni les autorisations ni les allocations n'entrent dans le calcul de l'attestation.

L'ensemble des opérations *requérant* une autorisation et l'axe sur lequel elles la requièrent,
comme l'ensemble des relations *portant* des limites de débit et les deux graphes — exposition et
mutation — qui s'y rattachent, sont des fonctions pures du manifeste. L'audit consiste à recalculer ces ensembles et à les confronter aux artefacts émis ;
toute pièce orpheline et toute obligation non couverte sont détectables mécaniquement.

**Périmètre de garantie — clause anti-survente, normative.** La garantie porte sur le manifeste et
sur la surface qui en dérive. Elle **ne couvre pas** : (a) les défauts de l'adaptateur ou du
moteur eux-mêmes ; (b) une connexion hors-bande au même datastore, contournant le runtime
attesté. Cette exclusion a une portée immédiate et concrète : la bibliothèque de validation par
liste blanche des versions `0.x`, encore consommée en production, **est** un tel chemin. La
garantie est donc **par datastore et conditionnée à l'exclusivité d'accès** — elle n'est
revendicable pour un datastore donné qu'une fois qu'aucun chemin legacy ne l'atteint plus (§3.9
P5, RC-41). Pour les datastores encore atteints par les deux voies, la revendication du §3.2 ne
s'applique pas, et l'affirmer serait faux ; (c) la
sémantique du discriminant de cloisonnement — si l'application attribue le mauvais identifiant de
tenant, QueryMe applique fidèlement la mauvaise règle ; (d) les canaux auxiliaires par inférence
ou agrégation, y compris ceux du substrat lui-même (cf. CVE-2025-8713, fuite de statistiques sous
RLS) et l'inférence obtenue en filtrant sur une relation **traversée sans être exposée** (§3.4) ;
(e) le **jugement** porté lors de l'émission d'une autorisation ou d'une allocation — QueryMe
garantit qu'aucune opération non bornée ne s'exécute sans décision tracée, il ne garantit pas que
la décision était bonne ; (f) la **collusion d'identités distinctes** et l'**extraction lente sous
le seuil de débit** — I9 borne une identité par fenêtre, il ne borne ni un ensemble coordonné
d'identités, dont le provisionnement relève du consommateur, ni une extraction patiente restant
sous la limite ; (g) le **refus collatéral entre co-locataires d'une identité partagée** — en
l'absence de sous-clé de partition déclarée (§3.4), le quota est collectif, et l'abus d'un
utilisateur dégrade le service des autres. QueryMe borne l'extraction, il ne répartit pas
équitablement le droit d'extraire ; (h) le **dommage sémantique d'une écriture autorisée** — I9
borne la *cadence* de mutation d'une identité, il ne garantit ni la justesse ni la réversibilité
d'un changement qu'un appelant avait le droit d'effectuer. Un quota ne remplace pas une
post-condition (I6).
**QueryMe garantit qu'une surface dont l'accès lui est exclusif ne peut pas être mal configurée ;
il ne garantit pas que le modèle de données est correct.** Toute communication publique reprend
cette clause (RC-24).

### 3.3 Autorisations budgétaires

Une opération que le compilateur ne peut pas borner statiquement n'est pas rejetée : elle est
classée **budgétée**. Elle compile, elle figure dans la surface attestée, et son exécution est
conditionnée à une **autorisation budgétaire** — un artefact signé, distinct de l'attestation.

**Déclenchement.** La classification est faite **par le compilateur, à la compilation**, et non par
observation du trafic. Le déclencheur est la première apparition de la forme d'opération **dans le
pipeline**, jamais sa première exécution sur des données réelles. Un déclenchement à l'exécution
serait défectueux dans ses deux branches : servir la première occurrence en attendant le verdict
laisse passer une exfiltration — une fois suffit ; la bloquer introduit une panne de production non
déterministe, déclenchée par la croissance des données, c'est-à-dire au moment le moins testable.

**Contenu.** Une autorisation lie `(hachage du manifeste, operation_id, classe de coût, profil,
plafond d'extraction, plafond de mutation, émetteur, expiration)` — un plafond peut être absent
lorsque l'axe correspondant est borné. La liaison au hachage du manifeste implique que **toute
modification du manifeste invalide toutes les autorisations**.

**Identité de forme.** La forme d'une opération est son `operation_id` dans le manifeste, jamais une
empreinte reconstruite à l'exécution (§3.8). Aucun appelant ne peut produire une variation d'une
opération, puisqu'il ne peut exprimer qu'un nom et des paramètres typés : la classe de contournement
par collision ou normalisation d'empreinte — mode d'échec historique des pare-feu de bases de
données en apprentissage — est absente par construction.

**Fonctions de coût — une par axe, universelles et majorantes.** Le manifeste déclare, par
opération, quels paramètres sont **porteurs de coût** et **deux expressions de coût** : un coût
d'**extraction**, majorant de ce que l'opération restitue, et un coût de **mutation**, majorant de
ce qu'elle modifie. Les deux sont **compilées** en fonctions embarquées dans l'artefact généré, au
même titre que le SQL : le runtime ne lit ni n'interprète jamais une expression, il évalue des
fonctions déjà compilées sur des paramètres typés.

Une expression unique serait indéfendable : une suppression de masse a un coût d'extraction nul et
un coût de mutation considérable. Majorer l'extraction sous-estimerait massivement la mutation — et
le sous-comptage est ce que le §3.4 nomme l'exploitable ; majorer la mutation surfacturerait toute
lecture d'un facteur arbitraire (R17). Un axe nul est parfaitement légitime : une lecture pure
déclare un coût de mutation nul, une écriture qui ne restitue rien un coût d'extraction nul.

Trois propriétés sont normatives et conditionnent tout le §3.4 :

1. **Toute opération porte les deux fonctions compilées**, bornée comme budgétée sur chaque axe. Le
   régime budgétaire ne fait qu'ajouter un plafond à l'axe concerné ; le régime de débit en a besoin
   pour toutes les opérations, la pagination nominale étant le chemin d'attaque principal en
   extraction (§1) et l'écriture répétée son pendant en mutation.
2. **Chaque fonction est un majorant sur son axe** : elle peut surestimer le coût réel, **jamais le
   sous-estimer**. En extraction, c'est acquis par la borne que I3 exige à la compilation ; en
   mutation, par la borne équivalente sur les lignes modifiées, ou à défaut par le plafond de
   l'autorisation, qui interrompt avant dépassement. La réservation du §3.4 est donc toujours
   supérieure ou égale au coût constaté sur chaque axe, ce qui rend les limites de débit inviolables
   plutôt qu'approximativement tenues (RC-38).
3. **Le régime budgétaire s'apprécie axe par axe** (I3, §3.2). Une opération non bornée sur un seul
   axe n'est budgétée que sur celui-là.

L'autorisation porte **un plafond par axe budgété**, jamais une valeur de paramètre particulière. Un
appel dont l'une des fonctions de coût dépasse le plafond de son axe est refusé **avant toute
exécution** — pas jugé « suspect ».

*Portée du déterminisme :* le refus budgétaire se calcule à partir de l'appel et de l'autorisation,
sans état. Il est déterministe **à autorisation et paramètres donnés, indépendamment de l'instant et
de l'historique** (RC-9). Le régime de débit ne possède pas cette propriété et n'y prétend pas
(§3.4).

**Émission.** L'émetteur est l'**évaluateur automatique**, contre le profil ; jamais le runtime, qui
ne détient aucune capacité de signature (RC-11). En `dev`, émission automatique à la compilation,
friction nulle pour l'auteur (§3.6) ; en `prod`, étape du pipeline de publication, automatique
contre critères déclarés ou soumise à revue humaine et seconde signature selon le palier (§3.6).

Le profil déclare en outre une **proportion maximale d'opérations budgétées par manifeste**. Le gate
de publication refuse un manifeste qui la dépasse : le refus porte sur la conception de la surface,
non sur une opération (RC-19, R11).

**Exécution.** Le runtime vérifie l'autorisation, évalue la fonction de coût compilée contre le
plafond, puis applique ce plafond comme un compteur de ressources. Il n'évalue aucune suspicion et
n'analyse aucune requête (§3.8).

L'interruption au plafond est une **annulation totale** : la transaction est annulée et l'appelant
ne reçoit **aucun résultat partiel**. Renvoyer les N−1 premières lignes offrirait une pagination
gratuite au-delà du plafond et annulerait la protection contre l'exfiltration que le plafond existe
pour porter. Le canal auxiliaire résiduel — le fait même de l'interruption et son instant — relève
de §3.10(5).

### 3.4 Allocations de débit

Le plafond unitaire du §3.3 borne un appel. Il ne borne rien face à un appelant **légitimement
authentifié** qui répète l'appel : un agent compromis ne franchit aucun contrôle d'identité, il
abuse d'un accès qu'il détient, et son chemin le plus large est la **pagination répétée d'une
opération bornée** — nominale, sans friction, sans autorisation (§1, driver 10). Le régime de débit
ferme ce scénario.

**Unité.** Le quota se compte en **coût cumulé**, jamais en nombre d'appels — la fonction de coût
compilée du §3.3 fournit déjà la monnaie, et compter des appels serait défait par des appels plus
gros.

**Clé.** Le compteur est clavé par `(identité, sous-clé, relation, axe, fenêtre)`, **non** par
opération. L'axe appartient à la clé : extraction et mutation sont deux compteurs distincts sur une
même relation, et les confondre reviendrait à laisser une cadence d'écriture consommer un quota de
lecture, ou l'inverse. Un quota par opération est défait en éclatant l'extraction sur plusieurs
opérations lisant la même relation ; l'objectif d'un adversaire est une relation, les opérations
n'en sont que des chemins.

**Assiette — deux graphes, deux axes.** Le compilateur dérive statiquement, pour chaque opération,
deux ensembles de relations :

- le **graphe d'exposition** — les relations dont au moins un champ figure dans la projection
  déclarée (I4), c'est-à-dire ce qui *sort* de l'opération. Il porte l'imputation en **extraction** ;
- le **graphe de mutation** — les relations que l'opération déclare modifier (I6), c'est-à-dire ce
  qui *change*. Il porte l'imputation en **mutation**.

Une opération mixte impute sur les deux axes. Une relation **traversée** — jointure, table de
correspondance, relation de filtrage — n'appartient à aucun des deux graphes et **n'est pas
imputée** : elle ne restitue ni ne modifie rien par cette opération ; l'imputer drainerait un quota
sans contrepartie et provoquerait des refus faux sur des surfaces légitimes, la jointure contre une
table de référence étant le cas courant.

Au sein d'un graphe, **chaque relation est imputée du coût intégral de l'exécution**, non d'une part.
Répartir permettrait de doubler son extraction — ou sa cadence de mutation — effective en empaquetant
plusieurs relations dans une même opération : le sur-comptage est la direction sûre, le sous-comptage
est l'exploitable.

*Fuite résiduelle, assumée :* filtrer sur une relation traversée renseigne par inférence sans rien
imputer. Canal exclu au §3.2 (d), instruit en §3.10(5).

**Granularité de l'identité.** La clé est l'identité effective au sens d'I2. Le manifeste peut
déclarer une **sous-clé de partition** afin que le quota descende sous un compte de service partagé.
Cette sous-clé provient **obligatoirement d'une revendication du justificatif vérifié, jamais d'un
paramètre d'appel** — même règle que le discriminant de I1 : autrement l'appelant choisit sa propre
sous-clé et s'octroie un quota illimité (RC-35). En l'absence de sous-clé déclarée, la limite est
**collective et assumée** : un utilisateur abusif prive ses co-locataires de leur quota (§3.2 (g),
R16).

**Déclaration.** Toute relation atteignable déclare **ses deux limites de débit** — coût cumulé
maximal par identité et par fenêtre, en extraction et en mutation. Un manifeste auquel manque l'une
des deux ne compile pas. Le profil borne les valeurs admissibles sur chaque axe séparément.

**Compilation.** Les deux limites de débit par relation, le **graphe d'exposition**, le **graphe de
mutation** et les sous-clés déclarées sont **compilés dans l'artefact généré**, au même titre que le
SQL et les deux fonctions de coût, et sont donc couverts par le hachage d'artefact du tuple attesté
(§3.2). Le runtime ne lit jamais le manifeste pour les obtenir (RC-7).

**Fenêtre.** La fenêtre est **glissante**. Une fenêtre fixe autorise un doublement au raccord de deux
fenêtres, soit une amplification ×2 de l'extraction — précisément ce que le mécanisme existe pour
borner. Toute approximation destinée à réduire l'état doit être **conservatrice** : elle peut refuser
à tort, jamais octroyer à tort (RC-32).

**Réservation avant exécution, réconciliation après.** L'imputation après coup ne borne rien sous
concurrence : N appels simultanés dont aucun n'est achevé n'ont encore rien imputé, passent tous, et
franchissent la limite d'un facteur N — un adversaire n'a aucune raison de sérialiser ses appels. Le
comptage procède donc en deux temps :

1. **Réservation.** Avant l'appel, le répartiteur évalue les **deux** fonctions de coût compilées et
   **réserve chaque valeur sur son axe** : le coût d'extraction sur les compteurs d'extraction des
   relations du graphe d'exposition, le coût de mutation sur les compteurs de mutation des relations
   du graphe de mutation. Si l'une quelconque de ces réservations porterait un compteur au-delà de
   sa limite applicable, l'opération est refusée **sans être exécutée** — le refus est global, une
   opération n'est jamais exécutée partiellement parce qu'un seul de ses axes passait.
2. **Réconciliation.** Après l'exécution, les coûts constatés sont réconciliés avec les réservations,
   axe par axe. Chaque fonction étant un **majorant sur son axe** (§3.3), le constaté est toujours
   inférieur ou égal au réservé : la réconciliation ne fait donc que **libérer un surplus**, jamais
   imputer un complément. C'est ce qui rend « l'extraction cumulée n'excède jamais la limite »
   littéralement vrai sur chaque axe, et non approximativement tenu.

**Atomicité.** La lecture, la vérification et la réservation forment une **opération atomique**.
Cette exigence vaut au sein d'un répartiteur comme entre plusieurs instances partageant l'état : une
réservation non atomique rouvre exactement la faille qu'elle est censée fermer. La colocalisation des
compteurs dans PostgreSQL rend cette atomicité naturelle — la réservation s'effectue dans la
transaction qui porte l'appel.

**Conservatisme des imprécisions.** Toute imprécision — approximation de fenêtre, réconciliation
partielle, réservation orpheline consécutive à un incident entre les deux temps — se résout **en
faveur du refus** : une réservation non réconciliée reste acquise au compteur et s'éteint avec sa
fenêtre. On ne libère jamais plus qu'on n'a réservé.

**Imputations particulières.** Une exécution interrompue au plafond **conserve sa réservation
intégrale** — sinon déclencher l'interruption offrirait un sondage gratuit. Elle conserve sa
*réservation*, et non le *plafond d'autorisation*, pour deux raisons : la réservation est le majorant
du coût de l'opération tentée, donc la grandeur juste, tandis que le plafond appartient à l'axe
budgétaire — une autre dimension — et l'imputer surchargerait d'un facteur arbitraire sans rapport
avec ce qui a été tenté ; et puisque RC-10 garantit qu'aucune ligne n'est restituée, **toute charge
non nulle suffit** à défaire le sondage gratuit, il n'y a rien à acheter en surchargeant. Un appel
refusé avant exécution est imputé d'un **coût minimal déclaré** — sinon sonder la frontière de la
fonction de coût ou de la limite de débit serait gratuit et illimité.

**Allocations.** Une **allocation de débit** est un artefact signé liant `(hachage du manifeste,
identité ou classe d'identité, sous-clé de partition ou joker, relation, axe, plafond de fenêtre,
profil, émetteur, expiration)`. Sous-clé et axe appartiennent au tuple parce que le compteur est
clavé par `(identité, sous-clé, relation, axe, fenêtre)` : sans la sous-clé, élargir le quota d'un
consommateur identifié par la sienne élargirait celui de toute l'identité parente, bien au-delà de
son objet ; sans l'axe, élargir la lecture d'un consommateur légitime lui ouvrirait le même volume
d'écriture, ce qui détruirait la raison d'être de la séparation des axes. Le joker élargit
l'ensemble des sous-clés d'une identité et n'est admissible que si le profil l'autorise. Elle
réutilise intégralement le cycle de vie de l'autorisation budgétaire : émise par
l'évaluateur contre le profil, jamais par le runtime ; invalidée par toute modification du
manifeste ; expirante et révocable.

Elle n'existe que pour **élargir** la limite déclarée au manifeste. En son absence, c'est cette
limite qui s'applique : **le cas sûr est le cas par défaut**, et aucune surface ne dépend de la
présence d'un artefact pour être protégée.

**État.** **Par défaut, l'état de comptage réside dans PostgreSQL**, aux côtés des données qu'il
protège. Ce choix n'est pas d'opportunité : il conserve une frontière de confiance unique,
n'introduit aucun composant supplémentaire à attester, rend la réservation atomique avec
l'exécution, et **n'ajoute aucune dépendance de disponibilité** — le compteur ne devient
indisponible que lorsque la base l'est déjà. Un magasin externe reste possible, mais constitue alors
une nouvelle frontière de confiance et un second composant portant la disponibilité de la surface :
il n'est admissible que si le profil le déclare, sous clearance §3.10(10).

**Indisponibilité du comptage.** Si l'état est injoignable, les opérations porteuses de débit sont
**refusées** — jamais servies non comptées. Un gate doté d'un interrupteur n'est pas un gate (R13).

**Déterminisme — et sa limite.** Un refus pour dépassement de débit dépend de l'instant et de
l'historique : deux appels identiques peuvent recevoir des verdicts différents. Ce n'est pas un
défaut mais la nature du mécanisme, et c'est pourquoi le déterminisme du §3.3 est borné au régime
budgétaire. La propriété du régime de débit est autre et tout aussi falsifiable : **à état de
compteur donné, la décision est déterministe et rejouable** (RC-31).

### 3.5 Architecture à trois couches — profils, pas forks

- **`queryme` (public, OSS)** — spécification, noyau d'invariants, évaluateur, compilateur, formats
  d'attestation, d'autorisation et d'allocation, adaptateurs, runtime de référence. Point d'extension
  unique : un **profil** est un ensemble versionné et signé d'invariants, de seuils, de politiques
  d'émission, d'une proportion maximale d'opérations budgétées par manifeste, de bornes admissibles
  pour les limites de débit et des classes de magasin de compteurs admissibles ; le noyau est
  agnostique au profil.
- **`queryme-blue` (public, OSS, publié *par* QueryMe)** — liaison de consommation pour Blue : nœuds
  Blue et gate de compilation garantissant qu'une règle Blue ne peut référencer que des opérations du
  manifeste. Le contrat `queryme-consumer` qu'elle implémente est **public** ; la liaison Blue en est
  une implémentation de référence parmi d'autres. Blue est un consommateur gaté, jamais le maître du
  standard.
- **`queryme-zapp` (privé, Zab)** — **n'est pas un fork**. Repo privé consommant `queryme` et
  `queryme-blue` en dépendances versionnées, apportant uniquement le profil de confiance Zab, le
  câblage du pipeline de publication et le devkit Zapp. Zéro source forkée. Une Zapp accède aux
  données **exclusivement** via `queryme-blue` : ni dépendance directe au cœur, ni pilote de
  datastore dans son graphe (RC-22).

**Test de frontière** : le changement modifie-t-il ce que le standard *signifie*, ou seulement le
sous-ensemble que Zab *exige* ? Sémantique → cœur public. Sous-ensemble, seuil ou politique de
confiance → profil Zab. Un besoin Zab touchant le cœur remonte upstream d'abord ; un correctif privé
temporaire est une exception documentée, datée et adossée à une issue upstream — jamais un fork
permanent.

### 3.6 Régime d'accès avant validation

Le gate s'applique **dans tous les environnements, sans interrupteur**. Un contournement en
développement devient le chemin d'exploitation en production.

Ce qui varie selon l'environnement est **le profil et les données**, jamais la présence du gate :

- développement et preview attestent contre un profil `dev` — même noyau, mêmes invariants, seuils
  relâchés, émission automatique des autorisations et allocations — et sont liés à un **jeu de
  données de développement provisionné** (synthétique ou anonymisé) ; jamais les données de
  production, jamais celles d'un autre tenant ;
- le profil appartenant au tuple attesté (§3.2), une attestation `dev` est structurellement
  inutilisable par un runtime `prod`, et il en va de même des autorisations et des allocations ;
- le gate de publication valide contre le profil `prod`.

Il n'existe donc **aucune fenêtre d'exposition** : la seule chose exposée avant validation est un jeu
de développement. L'auteur n'est jamais bloqué : le devkit ré-atteste localement en continu, et le
gate de publication n'est pas le premier contrôle mais **le dernier d'une série identique à profil
plus strict**.

Le devkit **ne détient jamais** de justificatif d'accès à un datastore de production ; le
provisionnement du jeu de développement est une responsabilité de `queryme-zapp`.

**Paliers de confiance.** Les Zapps produites par l'organisation et celles produites par des tiers
passent le **même gate**. Le palier détermine le **profil** appliqué — jeu d'invariants, seuils,
politiques d'émission, proportion maximale budgétée, bornes de débit, magasin de compteurs
admissible, exigence de revue manuelle, exigence d'une seconde signature — jamais l'applicabilité du
gate. Mécanisme uniforme, politique différenciée.

### 3.7 Dépôts, structure et domicile de ce document

QueryMe est une production **ZabLaboratory** : organisation GitHub unique, pas de scission
d'organisation entre le public et le privé.

- `queryme` : le dépôt **`ZabLaboratory/QueryMe` existant**, public depuis avril 2026, poursuivi
  sur son historique git — ni nouveau dépôt, ni réécriture d'historique. Son étage 1 est la
  structure ZabLaboratory existante.
- `queryme-blue` : dépôt **public** sous ZabLaboratory, créé en P2.
- `queryme-zapp` : dépôt **privé** sous ZabLaboratory, structure étage 1 Zab
  (`D:\Documents\Zab\QueryMeZapp\`) — il porte la doctrine de confiance et les secrets Zab.
- La ligne public/privé est une **ligne de dépôt**, jamais une ligne de base de code (§3.5).
- Aucun ADR de ce projet ne réside à l'étage 0 : `D:\Documents\docs\adr\` est réservé à la doctrine du
  fleet.

**Domicile canonique de ce document** : `QueryMe/docs/adr/001-positionnement-et-architecture.md`.
Le dépôt ne comporte aujourd'hui aucun répertoire `docs/` : ce document l'inaugure, et le numéro
`001` est libre. Un ADR de positionnement non versionné avec le code qu'il gouverne dérive sans
trace (RC-28).

**Supersession — sans objet.** Le `CLAUDE.md` du dépôt renvoie à un ADR `001-blueprint-db-access`
qui n'existe ni dans ce dépôt, ni dans aucun autre de l'organisation. Il n'y a donc rien à
superséder et le champ reste vide. La référence pendante est corrigée au même commit que
l'atterrissage du présent ADR (RC-28) : un dépôt public qui pointe vers un document inexistant
donne à lire une gouvernance qu'il n'a pas.

### 3.8 Runtime de référence — construction propriétaire

Le runtime de référence de la phase P1 est **écrit par le projet**. Aucun moteur tiers n'est composé
pour servir la surface attestée.

Trois options ont été instruites :

- **(C) QueryMe en proxy d'admission devant PostgREST.** Rejetée sans réserve : elle impose de
  ré-analyser le dialecte d'URL du moteur pour en re-dériver l'intention, créant un différentiel
  d'analyseurs entre le contrôle et l'exécution — la classe de contournement la mieux documentée du
  domaine.
- **(B) QueryMe génère un schéma d'exposition Postgres que PostgREST sert.** Techniquement viable —
  puisque QueryMe génère le SQL, I1, I2, I3, I4, I5, I6 et I7 sont portés par le schéma et les
  fonctions générés, non par la couche de service. Rejetée pour les motifs ci-dessous.
- **(A) Runtime propriétaire.** Retenue.

**Motifs du rejet de (B).**

1. *La surface servie ne serait pas fonction du seul manifeste.* La configuration de PostgREST expose
   au minimum `db-schemas`, `db-anon-role`, `db-max-rows`, `db-pre-request`, `db-root-spec`,
   `openapi-mode`, `db-plan-enabled`, `db-aggregates-enabled` et les réglages JWT — neuf leviers hors
   manifeste qui modifient ce qui est réellement exposé. `db-plan-enabled` publie les plans
   d'exécution via un en-tête `Accept` ; `db-aggregates-enabled` rouvre le coût non déterministe ;
   `db-pre-request` est le vecteur de CVE-2022-35912. Revendiquer « ne peut pas être mal configuré »
   exigerait d'énumérer et de neutraliser la surface de configuration d'un tiers, puis de la
   ré-énumérer à chaque version amont.
2. *I3 et I7 ne sont pas des propriétés du moteur.* `db-max-rows` est un plafond **global** appliqué à
   tous les points d'entrée, non une borne par opération, et son contournement pour les fonctions
   fait l'objet d'un ticket amont ouvert. I9 n'y a aucun équivalent : rien n'y borne le cumul d'un
   appelant dans le temps, et rien n'y permet de réserver avant exécution.
3. *Le conflit porte sur la revendication centrale.* En (B), la surface servie est fonction du
   manifeste **et** de la version et de la configuration du moteur. Une attestation n'y vaudrait que
   ce que vaut le modèle que QueryMe se fait d'un composant tiers — modèle dérivant à chaque version
   amont, dont la divergence est silencieuse, et pouvant invalider rétroactivement les attestations
   en circulation. C'est un passif de correction non borné placé sur l'objet même du produit.
4. *Asymétrie de l'échec.* Pour une passerelle ordinaire, un changement de comportement amont est un
   défaut. Pour QueryMe, c'est une garantie fausse alors qu'elle se déclarait prouvée — ce qui aggrave
   R2 et R7.
5. *La réutilisation obtenue serait mince et de commodité.* La surface du manifeste est un ensemble
   d'opérations **nommées** à paramètres typés. QueryMe ne conserverait de PostgREST que le transport
   HTTP, la vérification JWT, le pool de connexions et le marshalling de types, tout en devant
   désactiver le filtrage horizontal, le resource embedding, la projection, le tri arbitraire,
   `offset`/`limit`, les en-têtes `Prefer`, l'upsert en masse et l'introspection OpenAPI. Ce serait
   adopter une dépendance pour sa part de commodité en neutralisant sa part distinctive.

**Le composant qu'il serait effectivement déraisonnable de remplacer est PostgreSQL lui-même** —
planificateur, RLS, grants, types, transactions — et il est intégralement conservé. Il accueille
également, par défaut, l'état de comptage du §3.4, ce qui maintient une frontière de confiance
unique ; si un profil autorise un magasin externe, cette phrase cesse d'être exacte pour ce
déploiement — un second composant porte alors la disponibilité de la surface (R13).

**Devenir de l'existant.** Le choix de construire ne s'exerce plus dans l'abstrait mais sur un
scaffold en production. Trois décisions le règlent.

- `QueryDescriptor` — sa forme est **compatible** avec la direction retenue : clauses
  déclaratives sur un ensemble fermé, aucun SQL brut, c'est déjà la posture d'I5. Point de départ
  pour le vocabulaire du manifeste, non obstacle.
- `SchemaDescriptor` et l'endpoint `_schema` — **abandonnés** dans la nouvelle ligne. Découvrir
  une forme à l'exécution est l'inverse d'une surface compilée et attestée et contreviendrait à
  RC-7. La forme cesse d'être interrogée pour devenir déclarée.
- `compile_query()` — l'ébauche est **abandonnée, non reprise comme base de P1**. Compiler un
  descripteur en SQL au moment de l'appel placerait un compilateur de requêtes dans le chemin
  d'exécution : précisément la dérive que R1 surveille et que le tripwire interdit. Le nom
  survit, la sémantique est remplacée — la compilation devient manifeste → artefact, en amont,
  sous attestation.

*Ce réemploi partiel ne contredit pas le rejet de (B) ci-dessus.* Ce rejet porte sur la **composition d'un
moteur tiers** dont le comportement, la configuration et le calendrier de version échappent au
projet. Reprendre un vocabulaire issu du même dépôt, sous le même contrôle et dans le même
historique, n'est pas une composition : c'est de l'antériorité interne. La distinction tient à la
souveraineté sur le chemin d'exécution (driver 8), pas à l'origine des lignes.

**Trajectoire de version.** Le tag `v0.2.2` est **gelé et immuable** ; les six consommateurs qui
l'épinglent ne reçoivent rien de la nouvelle ligne tant qu'ils ne déplacent pas leur épinglage.
La nouvelle ligne est publiée sous un **bump majeur** — l'inversion sémantique interdit toute
prétention de compatibilité — et aucun consommateur n'est déplacé avant que sa cible existe. Pour
Blue, cette cible est `queryme-blue` (P2). Cette contrainte ordonne les phases (§3.9) ; ce n'est
pas une intention.

**Incidence sur la pile.** Le dépôt est aujourd'hui une bibliothèque Python pure, outillée `uv`,
`ruff` et `mypy` strict, sans entrées-sorties. Le noyau — spécification, évaluateur, compilateur
— reste de cette nature. Le répartiteur est un service : le dépôt change de nature en accueillant
les deux. Qu'il soit un distribuable distinct du même dépôt, et dans quelle pile, relève de
l'ouverture de P1 ; l'existant en est une donnée d'entrée, pas une décision déjà prise.

**Forme imposée au runtime.** Le runtime n'est pas un moteur de requête. Il comporte exactement deux
composants ; le magasin de compteurs n'en est pas un troisième mais une **dépendance
d'infrastructure**, de la même famille que le pool de connexions.

*Le compilateur* — manifeste → artefact généré : un schéma d'exposition Postgres fait de fonctions et
de grants, **sans aucune table ni vue atteignable**, plus les fonctions de coût compilées — majorantes
et définies pour **toute** opération (§3.3) —, les limites de débit, le graphe d'exposition, le
graphe de mutation et les sous-clés déclarées. Il réalise **I1** (prédicat de cloisonnement injecté dans chaque fonction),
**I2** (grants : aucun privilège au rôle anonyme), **I4** (projection close), **I5** (opérateurs
clos), **I6** (écritures en fonctions à pré/post-conditions, aucune capacité DML au rôle applicatif),
la part structurelle de **I9** (les deux limites déclarées par relation, le graphe d'exposition
calculé depuis la projection et le graphe de mutation calculé depuis les écritures déclarées), et matérialise le régime de **I3** et **I7** en classant les opérations bornées ou
budgétées (§3.3).

*Le répartiteur* — mince, et fermé à cette liste : vérifier l'attestation et la correspondance du
hachage d'artefact ; authentifier ; résoudre l'opération **par son nom** ; évaluer les **deux
fonctions de coût compilées** ; si l'opération est budgétée sur un axe, vérifier l'autorisation et
refuser avant exécution en cas de dépassement du plafond de cet axe ; **réserver atomiquement** le
coût d'extraction sur les compteurs d'extraction des relations du graphe d'exposition et le coût de
mutation sur les compteurs de mutation des relations du graphe de mutation, et refuser si l'une
quelconque des limites serait franchie (§3.4) ; lier les paramètres typés ;
appeler la fonction générée ; sérialiser ; **journaliser l'attribution** (opération, hachage du
manifeste, identité appelante, autorisation et allocation le cas échéant) — ce qui réalise **I8** ;
**réconcilier** chaque réservation avec le coût constaté de son axe, en ne libérant qu'un surplus ; compter les
ressources contre le plafond et **annuler totalement** au dépassement.

Le comptage de débit est donc une **étape du répartiteur**, pas un composant : sa logique tient dans
les points ci-dessus, et l'état qu'elle manipule réside par défaut dans PostgreSQL (§3.4).

Aucune planification, aucune analyse de filtre, aucun embedding, aucune projection : il n'y a rien à
planifier puisque le manifeste a déjà nommé chaque opération.

**Compilation déterministe — contrainte de conception.** Un même manifeste, compilé avec les mêmes
versions d'adaptateur, de jeu d'invariants et de compilateur, produit un artefact **identique octet
pour octet**. C'est l'implication directe de l'attestation comme fonction pure : sans elle, le hachage
d'artefact du tuple (§3.2) n'est pas reproductible et RC-3 est faux. Le compilateur n'admet donc
**aucune source d'horloge ni d'entropie**, et déclare un **ordre total sur toute émission** — ordre du
DDL, ordre des grants, ordre d'itération de tout ensemble, nommage des objets générés. Le
non-déterminisme est un piège concret et non hypothétique (R15).

**Passe attestante unique.** L'évaluateur **pilote** le compilateur : l'artefact attesté est celui
produit par la passe qui émet l'attestation, jamais celui d'une exécution séparée supposée
équivalente.

**Le runtime ne charge jamais le document manifeste.** Il ne connaît que l'artefact compilé et
l'attestation, laquelle porte les hachages du manifeste et de l'artefact. Aucune expression, aucune
règle, aucune limite, aucun schéma n'est interprété au service : tout ce qui devait être compris l'a
été à la compilation. C'est ce qui rend RC-7 mécaniquement vérifiable plutôt que déclaratif.

**Règle de réutilisation** : on réutilise des **bibliothèques** (pool de connexions, vérification JWT,
sérialisation, accès au magasin de compteurs), on ne compose pas des **moteurs**.

La résolution par nom a une conséquence qui dépasse la simplicité du répartiteur : **l'identité d'une
forme d'opération est structurelle**, c'est un nœud du manifeste et non une empreinte reconstruite à
l'exécution. C'est ce qui rend les régimes des §3.3 et §3.4 implémentables sans réintroduire d'analyse
de requête ; l'option (B) ne l'aurait pas permis.

**Tripwire.** Le runtime ne doit jamais **analyser** : ni une requête entrante pour en dériver
l'intention, ni le manifeste pour en dériver une règle. Toute opération est résolue par son nom, et
toute règle lui parvient déjà compilée. Il **mesure** les ressources d'une opération identifiée,
**évalue** une fonction de coût compilée et **compte** des coûts contre des compteurs clavés — aucune
des trois ne requiert de grammaire. Si le runtime a besoin d'un analyseur syntaxique, la conception
est fausse (RC-7).

**Expiration en service.** Une attestation qui expire alors que le runtime sert déjà provoque le
**refus de toute nouvelle opération**, les exécutions en cours étant menées à terme. Ni arrêt brutal
— ce serait un déni de service auto-infligé sur une surface conforme une seconde plus tôt — ni
poursuite silencieuse — ce serait un gate à interrupteur. L'événement est journalisé et constitue une
alarme d'exploitation (RC-30).

### 3.9 Phases

- **P0 — spécification seule, aucun runtime nouveau.** Le code existant demeure en place et sert
  ses consommateurs sous son tag gelé ; P0 n'y touche pas. Schéma du manifeste normatif de
  surface, noyau I1..I9 avec vérificateurs mécaniques, formats d'attestation, d'autorisation et
  d'allocation, les deux expressions de coût et leur contrainte de majoration par axe, définition
  des graphes d'exposition et de mutation. Livrable central : un **corpus de conformité** — un jeu
  de configurations volontairement vulnérables, toutes rejetées par l'évaluateur. C'est ce corpus
  qui rend la revendication falsifiable ; sans lui, P0 n'est pas livré. Cette phase établit
  également la trajectoire de version — gel de `v0.2.2`, bump majeur — et vérifie l'exécution
  effective de la CI du dépôt (R5).
- **P1 — compilateur et runtime de référence** (§3.8) sur Postgres, régimes budgétaire (§3.3) et de
  débit (§3.4) inclus, réservation atomique et compilation déterministe comprises.
- **P2 — `queryme-blue`** : contrat de consommation, liaison Blue, gate de compilation, **et
  chemin de migration de Blue** depuis son épinglage `queryme@v0.2.2`. La liaison sans le chemin
  ne libère pas le consommateur : la phase n'est pas livrée tant que Blue ne peut pas déplacer
  son épinglage sans perte de fonction (RC-40).
  L'ouverture de la phase provisionne également le substrat CI du nouveau dépôt : le runner qui
  sert `QueryMe` porte un label propre à ce dépôt et n'est pas hérité. Sans provisionnement
  équivalent — ou bascule assumée vers un runner hébergé — les jobs de `queryme-blue` resteront en
  file sans jamais échouer, panne silencieuse caractéristique du pool générique.
- **P3 — `queryme-zapp`** : profil de confiance Zab, séparation `dev`/`prod`, politiques d'émission
  des autorisations et des allocations par palier, devkit, gate du pipeline de publication.
- **P4 — second adaptateur** et palier de confiance tiers. Le travail d'un adaptateur est de compiler
  le manifeste vers une surface fermée native, jamais d'implémenter un langage de requête.
- **P5 — retrait du chemin legacy.** Migration des six consommateurs hors de la bibliothèque de
  validation par liste blanche, datastore par datastore. Cette phase n'est pas de l'hygiène :
  tant qu'un chemin legacy atteint un datastore, la revendication du §3.2 n'est pas applicable à
  celui-ci (§3.2 (b)). Elle conditionne donc la revendication, non la propreté du code.

La publication de la spécification démarre dès P1 ; sans publication précoce, la revendication de
standard ne tient pas.

**Hors périmètre, explicitement** : écosystème de connecteurs, authentification, stockage,
décisionnel, service hébergé, langage de requête destiné à l'utilisateur.

### 3.10 Sécurité — ce que cet ADR ne tranche pas

Les surfaces suivantes relèvent de Bastion et doivent être instruites **avant toute ligne de code de
la phase indiquée** :

1. *(P0)* modèle de signature et de révocation des attestations, autorisations et allocations —
   custody, rotation, et déterminisme ou non de la signature, dont dépend la portée exacte de RC-3 ;
2. *(P3)* manipulation des justificatifs par le devkit et isolation multi-tenant des jeux de
   développement ;
3. *(P3)* modèle de menace d'un auteur de Zapp **hostile** et chaîne d'approvisionnement du bundle ;
4. *(P0)* **solidité du jeu d'invariants lui-même** au regard de la revendication portée, y compris la
   validité de la partition ternaire du §3.2 et la légitimité de présenter I9 comme une garantie
   *appliquée* ;
5. *(P1)* canaux auxiliaires par inférence et agrégation : ceux du substrat, celui ouvert par
   l'interruption au plafond (§3.3), celui ouvert par le refus pour dépassement de débit et par
   l'observation des réservations (§3.4), et l'inférence obtenue en filtrant sur une relation
   **traversée sans être exposée** ;
6. *(P0)* **contenu du dépôt public, historique intégral et substrat d'exécution de sa CI**, au
   regard du profil de confiance Zab. Deux volets distincts.

   *Exposition.* Le dépôt est public depuis avril 2026 : il ne s'agit pas d'un gate préalable à
   une bascule mais d'un **audit rétroactif**, l'historique complet étant exposé depuis des mois.
   La revue porte sur les commits autant que sur l'état courant — secrets, identifiants, noms
   d'hôtes internes, schémas de services tiers — et son urgence est celle d'une exposition déjà
   réalisée.

   *Exécution.* La CI s'exécute sur un runner **self-hosted** de l'organisation. Un dépôt public
   qui se revendique standard ouvert (§3.1, RC-39) recevra des pull requests depuis des forks,
   dont l'exécution sur un tel runner ferait tourner du code non fiable sur une machine partagée.
   Le risque est aujourd'hui **nul en pratique et armé par construction** : il se réalise à la
   première contribution externe, c'est-à-dire au moment où le projet réussit. La politique
   d'exécution des PR de fork — approbation préalable obligatoire, runner éphémère et isolé, ou
   bascule vers un runner hébergé — est tranchée avant cette première contribution, jamais
   après ;
7. *(P1)* **sûreté du compilateur** (§3.8), surface d'attaque de premier plan dès lors que la garantie
   repose sur du SQL, des fonctions de coût et des limites générés ;
8. *(P1)* **cycle de vie des autorisations et des allocations** (§3.3, §3.4) : politiques d'émission
   par palier, révocation, expiration, invalidation sur changement de manifeste, et résistance à la
   banalisation de l'émission ;
9. *(P1)* **évaluation de la fonction de coût compilée sur des valeurs fournies par l'appelant** :
   débordement arithmétique, effets de bord, échappement au sens d'I5, et **solidité de la propriété
   de majoration** (§3.3), dont dépend l'inviolabilité de la limite de débit ;
10. *(P1)* **état de comptage du débit** (§3.4) : intégrité et confidentialité des compteurs — dont le
    volume double avec les deux axes —, atomicité effective de la réservation sur les deux axes
    conjointement, **criticité propre de l'axe de mutation**, dont les dépassements ont des
    conséquences irréversibles là où ceux d'extraction sont des fuites, résistance à la
    falsification, dérivation de la sous-clé depuis le justificatif vérifié, comportement sous
    partition et sur réservation orpheline, et — si le profil autorise un magasin externe — la
    frontière de confiance et la dépendance de disponibilité supplémentaires.

Une revendication de sécurité fausse est pire qu'aucune revendication. La revue adversariale de la
revendication elle-même — surface (4) — précède donc tout code **et** toute communication publique.

## 4. Consequences

- QueryMe cesse d'être comparable à PostgREST, Hasura ou Hive Gateway : le discours commercial et le
  plan de comparaison changent entièrement.
- La première itération ne livre aucun **produit nouveau** utilisable : une spécification, un
  évaluateur et un corpus. C'est le coût de la falsifiabilité. Mais un produit existe déjà et sert
  six services en production : le projet porte donc **deux charges simultanées**, construire la
  nouvelle ligne et migrer des consommateurs vivants. La seconde est la plus contrainte, car elle
  s'exerce sur des systèmes qui fonctionnent.
- Le dépôt change de nature : d'une bibliothèque pure sans entrées-sorties, il devient le porteur
  d'une spécification, d'un compilateur et d'un service de référence.
- La direction de la vérité s'inverse : la forme d'une surface cessera d'être interrogée à
  l'exécution pour être déclarée, compilée et attestée en amont. Aucune migration incrémentale
  n'existe entre les deux postures — c'est une bascule, protégée par la trajectoire de version.
- La revendication centrale devient **progressive** : elle s'acquiert datastore par datastore, à
  mesure que les chemins legacy se ferment (§3.2 (b), P5). Toute communication publique antérieure
  à P5 doit porter cette réserve.
- Le projet assume d'écrire son transport HTTP, sa liaison de paramètres et sa sérialisation.
- Le compilateur devient le composant critique : il porte le SQL généré, les fonctions de coût, les
  limites de débit et les deux graphes (exposition, mutation) ; sa correction porte la totalité de
  la garantie.
- **Le compilateur doit être déterministe au bit près**, ce qui contraint sa conception plus qu'un
  compilateur ordinaire : ordre total sur toute émission, aucune horloge, aucune entropie.
- **La fonction de coût doit majorer.** Une estimation trop généreuse consomme du quota pour rien, une
  estimation trop basse est interdite : l'auteur est structurellement poussé vers la prudence, et une
  opération mal estimée se paie en quota, jamais en fuite.
- **Le répartiteur détient un état de comptage** et réserve avant d'exécuter. Colocalisé dans
  PostgreSQL par défaut, cet état n'ajoute ni frontière de confiance ni dépendance de disponibilité ;
  un magasin externe, s'il est autorisé par le profil, ajoute les deux.
- **Le sur-comptage multi-relations est assumé, sur chaque axe** : une opération exposant trois
  relations consomme trois fois son coût d'extraction, une fois sur chacune ; une opération modifiant
  deux relations consomme deux fois son coût de mutation. C'est le prix de l'impossibilité de doubler
  son extraction — ou sa cadence d'écriture — en empaquetant des relations, et c'est aussi un signal
  de conception : une opération large est coûteuse en quota.
- Le régime de garantie n'est pas uniforme : six invariants structurels, deux budgétaires, un de
  débit — ce dernier **appliqué** et non impossible. La communication publique doit porter cette
  nuance, sous peine de survente.
- La permissivité est par opération et non à l'échelle de la surface : un manifeste massivement
  budgété est refusé.
- L'imputation aux seules relations exposées rend le quota lisible pour l'auteur : une jointure de
  référence ne consomme rien.
- En l'absence de sous-clé déclarée, le quota est **collectif** : un abus individuel dégrade le
  service de tout un tenant (§3.2 (g), R16).
- L'émission d'autorisations et d'allocations devient une charge opérationnelle récurrente du pipeline
  de publication : sa banalisation dégraderait le gate en label.
- L'état de privilège d'une surface devient explicitement énumérable — attestation, autorisations,
  allocations.
- L'annulation totale au plafond signifie qu'une opération légitime mais sous-estimée échoue sans
  résultat exploitable.
- Un consommateur légitime à fort volume doit obtenir une allocation : le débit devient une dimension
  de conception des Zapps, pas seulement une protection.
- Blue et les Zapps deviennent des consommateurs d'un contrat public ; toute évolution motivée par
  Blue doit se justifier hors de Blue.
- Le modèle de profils supprime la dette de fork mais introduit une API de profils à faire vivre et
  versionner.
- Deux structures étage 1 sous une organisation unique impliquent deux périmètres de secrets et un
  substrat CI à valider pour un dépôt public.
- Les auteurs de Zapp voient une contrainte permanente au lieu d'un contrôle final : l'ergonomie du
  devkit devient un facteur d'adoption de premier ordre.

## 5. Risks

- **R1 — Dérive vers la commodité.** Si P1 glisse vers la construction d'un moteur de requête, le
  projet a échoué. Détecteur : toute issue introduisant une **analyse de requête**, ou une **capacité
  d'expression** offerte à l'appelant qui n'est pas exigée par le manifeste. Une capacité de
  *contrôle* requise par un invariant — le comptage de débit d'I9 — n'est pas une dérive.
- **R2 — Revendication invérifiable ou survendue.** Aggravé par la partition ternaire du §3.2 et par
  le caractère *appliqué* du troisième temps de la revendication. Atténué par le corpus de
  conformité, la clause de périmètre et RC-24.
- **R3 — Standard à un seul adoptant.** Repli assumé : un produit à spécification ouverte.
  Falsifieur : RC-39.
- **R4 — Dérive de l'API de profils**, forme résiduelle de la dette de synchronisation.
- **R5 — Substrat CI.** Le pool self-hosted générique rejette les dépôts publics et le billing
  GitHub Actions de l'organisation est gelé. Le risque est **éteint pour `QueryMe`** — sa CI
  s'exécute et passe (dernier run observé 2026-06-20, `conclusion: success`) — mais par une cause
  différente de celle que supposait la formulation initiale : non par la gratuité des runners
  hébergés, mais par un runner self-hosted **provisionné spécifiquement pour ce dépôt**
  (`runs-on: [self-hosted, vps-ovh, zab-queryme]`), porteur d'un label qui lui est propre. La
  distinction compte, car ce qui est résolu est une configuration, non une propriété générale.

  Elle ne se transporte donc pas : `queryme-blue` (P2) exigera son propre provisionnement ou une
  bascule assumée vers un runner hébergé, et à défaut ses jobs resteront en file sans jamais
  échouer — panne silencieuse, plus coûteuse qu'un échec franc. Le corollaire de sécurité de ce
  substrat — exécution de PR de fork sur une machine de l'organisation — relève de §3.10(6).
- **R6 — Gravité de Zab.** Seul le test de frontière du §3.5 s'y oppose. L'organisation unique accroît
  ce risque : la séparation ne repose plus que sur la discipline.
- **R7 — Responsabilité juridique** attachée à une revendication de sécurité publique.
- **R8 — Fuites du substrat.** La garantie se superpose à une base qui a ses propres fuites sous RLS
  (classe CVE-2025-8713) ; §3.2 (d) l'assume.
- **R9 — Nom.** L'occupation relevée en amont était **interne** : `ZabLaboratory/QueryMe` est le
  projet lui-même, il n'y a donc pas de collision d'organisation. Le résiduel porte sur les
  registres de paquets et l'antériorité de marque, ni l'un ni l'autre vérifiés — et il pèse plus
  lourd ici qu'ailleurs, un projet qui se revendique standard public s'exposant à une contestation
  de nom que rien ne permet aujourd'hui d'écarter.
- **R10 — Coût et tentation du runtime propriétaire.** La forme du §3.8 et RC-7 sont les seules digues.
- **R11 — Banalisation de l'émission.** Atténuation : proportion maximale déclarée au profil (RC-19).
- **R12 — Substitution du budget à la conception.** Atténuation : friction asymétrique délibérée.
- **R13 — Disponibilité du comptage.** Le fail-closed du §3.4 transforme une panne de l'état de
  comptage en indisponibilité de la surface, ce qui ouvre un déni de service à qui dégrade ce
  composant. La colocalisation dans PostgreSQL réduit fortement ce risque — le compteur ne tombe que
  si la base tombe — sans l'annuler : un magasin externe autorisé par un profil le réintroduit
  intégralement, et déplace une part de la disponibilité de la surface sur un second composant. La
  variante fail-open est exclue : elle détruirait I9.
- **R14 — Collusion et extraction lente.** I9 borne une identité par fenêtre ; un ensemble coordonné
  d'identités ou une extraction patiente sous le seuil restent hors de portée (§3.2 (f)).
- **R15 — Non-déterminisme du compilateur.** Ordre du DDL, horodatage, itération d'ensembles non
  ordonnés, nommage généré : pièges concrets qui invalideraient silencieusement le hachage d'artefact.
  Atténuation : RC-29, exécuté en CI dès P1.
- **R16 — Quota collectif.** En l'absence de sous-clé déclarée, un utilisateur abusif consomme le
  quota de tout un tenant — un déni de service latéral entre utilisateurs d'une même Zapp.
  Atténuation : sous-clé de partition déclarée dès que le justificatif porte la revendication
  nécessaire (§3.4, §3.2 (g)).
- **R17 — Estimation de coût trop grossière.** Une fonction de coût majorante mais très éloignée du
  réel épuise les quotas de consommateurs légitimes et rend le produit inutilisable. La contrainte de
  majoration est non négociable ; sa *finesse* est un enjeu de conception de P0 et de qualité
  d'auteur, mesuré par l'écart réservation/constaté sur chaque axe. Le risque est **asymétrique** :
  une lecture refusée à tort se retente à la fenêtre suivante, une écriture refusée à tort peut
  interrompre un traitement métier en cours et laisser un état partiel que le produit ne sait pas
  réparer. La finesse de l'estimation de mutation mérite donc plus d'attention que celle
  d'extraction.
- **R18 — Rupture des consommateurs existants.** Six services de production dépendent de la ligne
  `0.x`, dont Blue, que cet ADR fait par ailleurs consommer `queryme-blue`. Une publication sur la
  ligne épinglée, un déplacement de tag ou une migration de Blue avant P2 les casserait.
  Atténuation : gel de `v0.2.2`, bump majeur, migration après existence de la cible (§3.8, RC-40).
  Le risque est procédural, non technique : il se réalise par précipitation, pas par défaut de
  conception.

## 6. Resolution criteria

Chaque critère porte un moyen de vérification. Un critère sans vérification n'en est pas un.

- **RC-1** — Le porteur a validé explicitement le reframe du §3.1. *Vérif :* trace écrite dans le fil
  de décision de l'ADR.
- **RC-2** — Le corpus de conformité contient au minimum dix configurations vulnérables distinctes,
  dont la policy « toutes lignes exposées à tout utilisateur authentifié » observée sur Supabase, et
  au moins trois configurations conformes de référence. *Vérif :* l'évaluateur rejette les dix et
  accepte les trois, en CI.
- **RC-3** — Deux exécutions indépendantes de la passe attestante sur les mêmes entrées produisent une
  **charge utile d'attestation** octet pour octet identique. Le déterminisme de la *signature* n'est
  pas présumé et relève de §3.10(1). *Vérif :* comparaison binaire en CI.
- **RC-4** — Le runtime refuse de démarrer si l'attestation est absente, expirée, d'un profil
  différent, si le hachage du manifeste ne correspond pas, ou si le hachage de l'artefact ne
  correspond pas à l'artefact chargé. *Vérif :* cinq tests, un par cas.
- **RC-5** — Une attestation de profil `dev` est rejetée par un runtime `prod`. *Vérif :* test dédié.
- **RC-6** — Un champ, une relation ou un opérateur absent du manifeste est inatteignable. *Vérif :*
  absence de chemin d'exécution dans l'artefact généré, pas refus de policy à l'exécution.
- **RC-7** — Le runtime ne comporte **aucun analyseur** : il ne charge jamais le document manifeste et
  ne reçoit aucune règle, aucune limite ni aucune expression non compilée. *Vérif :* absence de toute
  bibliothèque d'analyse syntaxique dans l'arbre de dépendances du répartiteur ; absence de lecture du
  manifeste dans les entrées-sorties du processus de service, observée à l'exécution. Ni la mesure de
  ressources, ni l'évaluation de la fonction de coût compilée, ni le comptage de débit ne constituent
  une analyse.
- **RC-8** — Le schéma d'exposition généré ne contient aucune table ni vue atteignable par le rôle
  applicatif, et ce rôle ne détient aucun privilège `INSERT`, `UPDATE` ou `DELETE`. *Vérif :*
  énumération mécanique des grants après compilation.
- **RC-9** *(régime budgétaire uniquement)* — Une opération budgétée sans autorisation valide n'est
  pas servie ; une opération dont l'une des fonctions de coût dépasse le plafond de son axe est refusée **avant
  exécution** ; une exécution qui dépasse son plafond est interrompue. *Vérif :* trois tests ; le
  refus est déterministe **à autorisation et paramètres donnés, indépendamment de l'instant et de
  l'historique** — à distinguer du déterminisme à état de compteur donné du régime de débit (RC-31).
- **RC-10** — Une opération interrompue au plafond ne renvoie **aucune ligne**. *Vérif :* test
  d'exfiltration — une opération volontairement au-delà du plafond ne restitue rien d'exploitable, et
  sa répétition n'accumule aucun résultat.
- **RC-11** — Le runtime ne détient aucune capacité d'émission d'autorisation ni d'allocation.
  *Vérif :* aucune clé de signature résoluble depuis le périmètre de justificatifs du processus de
  service ; absence de la bibliothèque d'émission dans son arbre de dépendances ; revue de code.
- **RC-12 (I1)** — Un manifeste comportant une seule relation atteignable sans liaison de
  cloisonnement est rejeté. *Vérif :* test d'atteignabilité sur le graphe ; et, sur le corpus, aucune
  opération ne restitue une ligne d'un autre tenant que celui de l'identité appelante.
- **RC-13 (I2)** — L'énumération rôles × relations atteignables donne l'ensemble vide pour toute
  identité non authentifiée, et un manifeste accordant un privilège à un rôle anonyme est rejeté.
  *Vérif :* énumération mécanique post-compilation + test de rejet.
- **RC-14 (I6)** — Toute opération d'écriture déclare ses pré/post-conditions ; un manifeste comportant
  une écriture sans post-condition est rejeté ; les post-conditions sont vérifiées dans le corps de la
  fonction générée. *Vérif :* test de rejet + inspection de l'artefact + test d'échec de
  post-condition provoquant l'annulation.
- **RC-15 (I8)** — Toute exécution servie est attribuable. *Vérif :* sur un corpus d'exécutions
  rejouées, reconstruction à 100 % du tuple (opération, hachage du manifeste, identité appelante,
  autorisation et allocation le cas échéant) depuis le seul journal ; zéro exécution non attribuée.
- **RC-16 (I9)** — Un manifeste comportant une relation atteignable dépourvue de l'une ou l'autre de
  ses deux limites — extraction, mutation — est rejeté. *Vérif :* deux tests de rejet, un par axe ;
  l'ensemble des relations portant des limites, le graphe d'exposition et le graphe de mutation sont
  recalculables depuis le manifeste seul, et figurent dans l'artefact généré donc sous le hachage
  attesté.
- **RC-17** — L'ensemble des opérations requérant une autorisation est reproductible. *Vérif :* deux
  compilations indépendantes du même manifeste classent exactement les mêmes opérations comme
  budgétées.
- **RC-18** — Une modification du manifeste invalide toutes les autorisations et allocations
  préexistantes ; un artefact émis pour un profil est rejeté par un runtime d'un autre profil ; un
  artefact expiré est rejeté. *Vérif :* trois tests dédiés, appliqués aux deux familles.
- **RC-19** — Le gate de publication refuse un manifeste dépassant la proportion maximale d'opérations
  budgétées déclarée au profil. *Vérif :* test de rejet avec un manifeste au-delà du seuil (R11).
- **RC-20** — L'audit croisé est mécanisable. *Vérif :* un outil compare **d'abord** le hachage du
  manifeste qui lui est fourni à celui porté par l'attestation en service — un audit mené sur un
  manifeste divergent ne prouve rien — puis recalcule les obligations (autorisations requises,
  relations à limiter, graphe d'exposition, graphe de mutation) et les confronte aux artefacts émis,
  signalant toute pièce orpheline comme toute obligation non couverte.
- **RC-21** — `queryme-zapp` ne contient aucune source dupliquée depuis `queryme` ou `queryme-blue`.
  *Vérif :* contrôle mécanique en CI.
- **RC-22** — **À l'issue de P3**, une Zapp n'accède aux données que via `queryme-blue`. *Vérif :*
  contrôle du graphe de dépendances et d'imports du bundle — ni dépendance directe au cœur, ni
  pilote de datastore. Ce critère est **violé à l'ouverture** : Blue dépend aujourd'hui du cœur en
  direct. C'est une dette datée, non une exigence tenue — elle se solde en P2 pour Blue et en P3
  pour les Zapps, et son non-respect à ces échéances est un échec de phase.
- **RC-23** — Le devkit ne peut, par construction, résoudre aucun justificatif de production.
  *Vérif :* test + revue Bastion (§3.10(2)).
- **RC-24** — La clause anti-survente du §3.2 est reproduite verbatim sur tous les points d'entrée
  publics (README, page de spécification), y compris la mention que le troisième temps de la
  revendication est *appliqué* et non impossible. *Vérif :* contrôle de présence du texte en CI, échec
  du build documentaire en cas d'absence ou de divergence.
- **RC-25** — La CI s'exécute effectivement sur **chaque** dépôt public du projet, dès l'ouverture
  de la phase qui le crée. *Vérif :* pour `QueryMe`, acquis — exécution verte observée sur son
  runner dédié, pas une hypothèse (R5). Pour `queryme-blue`, l'ouverture de P2 démontre une
  exécution verte **avant** tout travail de fond : un job resté en file n'échoue pas et ne signale
  rien, l'absence de résultat doit donc être constatée explicitement et non déduite du silence. La
  politique d'exécution des PR de fork est arrêtée pour les deux dépôts avant la première
  contribution externe (§3.10(6)).
- **RC-26** — Bastion a rendu une clearance ou un veto explicite sur chacune des **dix** surfaces du
  §3.10, avant le code de la phase indiquée. *Vérif :* dix verdicts tracés.
- **RC-27** — Le second adaptateur de P4 réutilise le noyau d'invariants sans le modifier. *Vérif :*
  diff du noyau nul entre P3 et P4.
- **RC-28** — Ce document réside à son domicile canonique et le dépôt ne conserve aucune référence
  normative pendante. *Vérif :* le fichier est présent en
  `docs/adr/001-positionnement-et-architecture.md` ; le commit est signé et porte les trailers
  requis ; **dans le même commit**, la référence du `CLAUDE.md` du dépôt à
  `001-blueprint-db-access` est remplacée par le chemin réel de ce document. La formulation
  antérieure — « premier commit du dépôt » — était fausse, le dépôt préexistant à cet ADR ; ce
  qu'elle garantissait est le co-versionnement de l'ADR et du code qu'il gouverne, ce que ce
  critère énonce directement.
- **RC-29** — La compilation est déterministe. *Vérif :* deux compilations indépendantes du même
  manifeste, mêmes versions, produisent un artefact **identique octet pour octet** ; contrôle en CI dès
  P1, sur un manifeste comportant plusieurs relations, plusieurs grants et plusieurs opérations, afin
  d'exercer les ordres d'émission (R15).
- **RC-30** — Une attestation expirant en cours de service provoque le refus de toute nouvelle
  opération, sans interrompre les exécutions en cours, et journalise l'événement. *Vérif :* test
  d'expiration en service.
- **RC-31 (I9)** — Le débit est effectivement borné sur les chemins réels d'extraction et de
  mutation, et déterministe à état donné. *Vérif :* quatre scénarios, tous menés à leur terme.
  (a) **Pagination nominale** — une opération **bornée**, sans autorisation ni friction, est paginée en
  boucle sur une relation entière : l'extraction cumulée s'arrête à la limite de la relation et les
  appels suivants sont refusés. C'est le chemin d'attaque principal du §1 ;
  (b) **Extraction éclatée** — plusieurs opérations **distinctes** exposant la même relation sont
  alternées : le cumul est agrégé sur le compteur de la relation, non par opération, et la limite est
  atteinte au même volume total qu'en (a). C'est la justification du clavage par relation ;
  (c) **Rejeu** — deux rejeux depuis le même état de compteur produisent des décisions identiques ;
  (d) **Mutation répétée** — une opération d'écriture est répétée sur une même relation : le cumul
  s'agrège sur le compteur de **mutation** de cette relation, indépendamment de son compteur
  d'extraction, et les appels sont refusés à la limite de mutation alors même que la limite
  d'extraction reste intacte. Puis le symétrique : une lecture répétée jusqu'à épuisement du quota
  d'extraction laisse le quota de mutation intact et n'empêche pas une écriture.
  Dans les quatre cas : une opération interrompue conserve sa réservation intégrale, et un appel refusé
  avant exécution est imputé du coût minimal déclaré.
- **RC-32** — Toute approximation de la fenêtre glissante est conservatrice. *Vérif :* sur un jeu de
  séquences adverses incluant les raccords de fenêtre, l'implémentation ne laisse jamais passer un coût
  cumulé supérieur à la limite ; elle peut refuser plus tôt, jamais plus tard.
- **RC-33** — L'indisponibilité de l'état de comptage entraîne le refus des opérations porteuses de
  débit. *Vérif :* le test ne peut pas s'exécuter en rendant la base injoignable — en configuration
  par défaut, les compteurs y étant colocalisés, cet état est indiscernable d'une surface totalement
  indisponible et le critère serait vide. Il s'exerce donc par **injection de faute sur le chemin
  d'accès aux compteurs**, la base restant par ailleurs servante : aucune opération porteuse de débit
  n'est alors servie non comptée. Lorsqu'un profil autorise un magasin externe, le même critère
  s'exerce en rendant ce magasin injoignable.
- **RC-34 (I9)** — L'imputation suit les deux graphes et rien d'autre. *Vérif :* (a) sur une opération
  joignant une relation exposée et une relation traversée sans exposition ni mutation, le compteur
  d'extraction de la seconde reste inchangé tandis que celui de la première est imputé du coût
  intégral ; (b) sur une opération exposant deux relations, chacune est imputée du coût intégral ;
  (c) sur une opération d'écriture, le compteur de **mutation** de la relation modifiée est imputé et
  son compteur d'extraction reste inchangé ; (d) sur une opération mixte, les deux axes sont imputés.
- **RC-35 (I9)** — La sous-clé de partition provient exclusivement d'une revendication du justificatif
  vérifié. *Vérif :* un manifeste déclarant une sous-clé issue d'un paramètre d'appel est rejeté ; et
  un appelant modifiant la valeur d'un paramètre ne change pas le compteur auquel il est imputé.
- **RC-36 (I9)** — La limite tient sous concurrence. *Vérif :* N appels lancés simultanément par la
  même identité sur une relation dont le quota résiduel n'en autorise que k < N — exactement k
  aboutissent, N−k sont refusés, et le compteur final n'excède pas la limite. Test répété sur
  plusieurs instances de répartiteur partageant le même état, afin d'exercer l'atomicité
  inter-répliques.
- **RC-37** — La réconciliation ne libère qu'un surplus. *Vérif :* sur un corpus d'exécutions, le
  montant libéré après réconciliation est toujours positif ou nul ; une interruption entre réservation
  et réconciliation laisse la réservation acquise, et son extinction n'a lieu qu'avec sa fenêtre.
- **RC-38** — Chaque fonction de coût est majorante sur son axe, **par construction**. *Vérif :* la
  majoration n'est pas établie par le test mais par le compilateur — en extraction, borne dérivée à
  la compilation pour le régime borné (I3) ; en mutation, borne équivalente sur les lignes modifiées ;
  et sur tout axe budgété, plafond dur interrompant avant dépassement (§3.3). La vérification est donc
  double : (a) la construction elle-même est documentée et revue au titre de §3.10(9), qui en porte la
  charge, **séparément pour chaque axe** — la construction de l'axe de mutation n'hérite rien de celle
  de l'axe d'extraction ; (b) un corpus d'opérations couvrant les deux régimes et les deux axes, sur
  une plage de paramètres porteurs de coût, sert de **falsifieur** — le coût constaté doit toujours
  être inférieur ou égal au coût évalué, sur chaque axe pris isolément. Un contre-exemple ne signale
  pas un cas limite : il invalide la construction, et avec elle l'inviolabilité des limites du §3.4.
- **RC-39** — À la fin de P4, au moins un adoptant extérieur à ZabLaboratory exploite un manifeste
  attesté en production. *Vérif :* référence publique vérifiable. À défaut, la revendication de
  « standard » est retirée de toute communication au profit de « produit à spécification ouverte »
  (R3).
- **RC-40** — Aucun consommateur existant n'est cassé par la nouvelle ligne. *Vérif :* (a) le tag
  `v0.2.2` désigne le même objet git avant et après les travaux de P0 à P5 — il n'est jamais
  déplacé ; (b) une installation `queryme @ git+…@v0.2.2` résout le même contenu qu'avant, testée
  après chaque phase ; (c) la nouvelle ligne est publiée sous un numéro de version majeur distinct ;
  (d) à la livraison de P2, Blue déplace son épinglage vers `queryme-blue` sans perte de fonction,
  démontré par sa propre suite de tests et non par déclaration.
- **RC-41** — La revendication n'est portée que sur les datastores dont l'accès est exclusif.
  *Vérif :* (a) inventaire côté serveur des principals détenant un droit de connexion au
  datastore — énumération des rôles et de leurs grants, confrontée aux connexions observées —
  ne révélant aucun principal hors du runtime attesté ; (b) aucun consommateur ne résout la
  bibliothèque de validation `0.x` — contrôlé sur les graphes de dépendances des six services et
  sur l'usage effectif du tag `v0.2.2` ; (c) la documentation publique n'énonce la revendication
  du §3.2 que pour les datastores établis par (a) et (b). Un datastore encore atteint par les deux
  voies est explicitement listé comme non couvert (§3.2 (b), P5).

## Amendment 1 — Ancre de confiance, révocation par durée de vie, et correction de la partition des régimes

- **Status**: accepted
- **Date**: 2026-08-15
- **Decided**: 2026-08-15
- **Deciders**: @ClodoCapeo
- **Author**: Atlas

> Bastion a instruit les surfaces §3.10(1) — signature et révocation — et §3.10(4) — solidité du
> jeu d'invariants et de la partition ternaire — et rendu un **veto sur les deux**
> (`ZabLaboratory/QueryMe#6`, thread `bastion-adr-001-invariants`, ADR lu @ `17e78871d`). Les
> deux vetos sont étroits : aucun ne demande de refonte d'architecture. Ce qui est bloqué, ce sont
> des affirmations aujourd'hui fausses ou indécidées dans un document dont RC-24 impose la
> reproduction publique verbatim, plus deux points de schéma et de format qui déterminent du code
> P0 et doivent donc atterrir avant lui.
>
> Le présent amendement porte les huit décisions de §3.10(1) (A1–A8) et les neuf points de
> §3.10(4) (B1–B9). Il **corrige** B5 et **atténue** B9 plutôt que d'en accepter le risque : pour
> B5 parce que RC-19 est l'unique atténuation de R11 et qu'une atténuation contournable par
> découpage de document n'en est pas une ; pour B9 sur décision explicite du porteur — patch
> `v0.2.3`, pas acceptation de risque. La distinction entre *corriger* et *atténuer* est
> normative : le patch borne l'appel unitaire, il ne borne pas l'extraction cumulée, dont la
> fermeture s'obtient par le **passage sur une surface attestée où I9 s'applique** — ce qui
> suppose la sortie de la ligne `0.x`, consommateur par consommateur : P2 pour Blue, P5 pour les
> cinq autres, I9 (P1) n'en étant que la précondition (R20).
>
> Il ne modifie ni le positionnement du §3.1, ni le choix de construction du §3.8, ni l'ordre des
> phases du §3.9. Les révisions antérieures ne sont pas réécrites.

### A1. Contexte

L'ADR 001 déférait explicitement la signature à §3.10(1) — RC-3 : « Le déterminisme de la
*signature* n'est pas présumé et relève de §3.10(1) ». L'instruction est rendue ; ses décisions
appartiennent désormais au texte normatif et non au rapport qui les porte.

Deux constats de l'instruction dépassent le périmètre attendu et motivent la moitié des
modifications ci-dessous :

1. **En aval de l'ancre de confiance, toute la revendication « non exprimable par construction »
   se réduit à une propriété unique : la clé de l'évaluateur n'a pas été détournée.** Le
   répartiteur ne voit jamais le manifeste (RC-7) ; il n'a donc aucun point de comparaison
   indépendant. C'est une conception légitime, mais elle n'était nommée nulle part, dans un
   document que RC-24 rend public mot pour mot.
2. **Le régime structurel dépend de deux vérifications à l'exécution** — celle du justificatif
   (I2) et celle de l'attestation — alors que sa définition affirmait ne dépendre d'aucun
   composant à l'exécution. La distinction visée était juste ; sa formulation était fausse.

### A2. Modifications littérales du texte normatif

Chaque item donne l'ancre dans la révision `17e78871d` et le texte de remplacement. Vigil
applique ; aucun texte n'est reformulé au-delà de ce qui est écrit ici.

> **Ordre d'application** : appliquer les items par **numéro de ligne décroissant**, afin qu'aucune
> insertion ne décale l'ancre d'un item non encore appliqué. La numérotation M1…M28 est un
> identifiant, pas un ordre d'exécution.

#### M1 — §3.2, bullet « Régime structurel » (l.154-156)

**Remplacer par :**

> - **Régime structurel** (I2, I4, I5, I6 — et I1 sous la réserve ci-dessous) — ce qui viole est
>   **non exprimable** : absent de la surface exécutable, sans voie de sortie ni dérogation. La
>   garantie ne dépend d'**aucune décision de politique** à l'exécution. Elle dépend en revanche de
>   deux propriétés du répartiteur, nommées ici et non présumées : la **vérification du
>   justificatif** dont dérive l'identité effective (I2), et la **vérification de l'attestation**
>   contre l'ancre de confiance du profil (§3.10(1)). Un défaut de l'une ou de l'autre défait le
>   régime en amont de toute question d'expressivité.

#### M2 — §3.2, bullet « Régime budgétaire » (l.157-160)

**Remplacer** « aucune opération non bornée ne s'exécute sans décision tracée, attribuable et
révocable » **par** « aucune opération non bornée ne s'exécute sans décision tracée, attribuable
et **à durée de vie bornée** ». *(A5 — voir M15.)*

#### M3 — §3.2, bullet « Régime de débit » (l.161-166)

**Remplacer l'en-tête** « **Régime de débit** (I9) » **par** « **Régime appliqué** (I8, I9) », et
**ajouter** en fin de bullet :

> I8 y figure parce que l'auditabilité est **réalisée par le répartiteur** (§3.8) et non par
> l'inexpressivité : elle repose sur la disponibilité du puits d'attribution, au même titre que I9
> repose sur celle du comptage. La ranger parmi les impossibilités serait de la survente au sens
> exact de R2.

#### M4 — §3.2, après la liste des trois régimes (insertion après l.169)

**Insérer :**

> **Étiquetage.** Un invariant porte donc un **couple** — régime de l'*obligation de déclarer*,
> toujours structurel, et régime du *dépassement*, qui seul varie. Les étiquettes ci-dessous
> nomment le second ; le premier est invariablement structurel et ne se déclare pas invariant par
> invariant.

#### M5 — §3.2, revendication en trois temps (l.171-176)

**Ajouter** en fin de paragraphe :

> Aucun des trois temps ne porte l'auditabilité : elle relève du **régime appliqué**, au même titre
> que le débit, et ne peut donc être annoncée comme une impossibilité.

#### M6 — §3.2, invariant I1 (l.178-181)

**Ajouter** à la fin du bullet :

> Le long de tout chemin de jointure d'une opération, les prédicats de cloisonnement doivent être
> prouvablement dérivés de **la même revendication** du justificatif vérifié. Un manifeste joignant
> deux relations cloisonnées sur des discriminants dont l'identité n'est pas établie est **rejeté**,
> à moins qu'une **relation de dérivation** entre les deux discriminants soit déclarée et
> elle-même cloisonnée.

*(B4. L'atteignabilité prouve que chaque relation porte un prédicat sur un discriminant déclaré,
non que ce soit le même : deux relations jointes, l'une cloisonnée sur `tenant_id`, l'autre sur
`org_id`, satisfont I1 mécaniquement pendant que la jointure franchit la frontière. Toute
hiérarchie multi-tenant réelle produit ce cas sans malveillance. C'est un renforcement
d'invariant : il change le schéma du manifeste, donc il est P0.)*

#### M7 — §3.2, invariant I8 (l.212-215)

**Remplacer l'étiquette** *(structurel)* **par** *(appliqué)*, et **ajouter** :

> L'indisponibilité du puits d'attribution entraîne le **refus** des opérations, même posture
> fail-closed que le comptage du §3.4 : une opération servie sans être attribuable défait I8 dans
> le silence, et c'est précisément le chemin qu'emprunte l'identité légitime compromise du driver
> 10 — bornée en volume par I9, mais sans trace.
>
> Ce fail-closed n'a de sens qu'assorti de la même atténuation que celui du comptage : **par
> défaut, le puits d'attribution réside dans PostgreSQL**, écrit dans la transaction qui porte
> l'appel, aux côtés des données et des compteurs. Il n'ajoute alors ni frontière de confiance ni
> dépendance de disponibilité — il ne devient indisponible que lorsque la base l'est déjà. Un puits
> externe reste possible et n'est admissible que si le profil le déclare, sous clearance
> §3.10(10) ; il réintroduit alors intégralement le risque de déni de service par dégradation d'un
> composant tiers (R21).

#### M8 — §3.2, invariant I3, nuance sur la permissivité (l.194-198)

**Remplacer** « Un profil déclare une proportion maximale d'opérations budgétées par manifeste
(§3.3, §3.5) ; un manifeste qui la dépasse est refusé **en tant que manifeste**. » **par :**

> Un profil déclare un plafond d'opérations budgétées **en proportion et en valeur absolue**,
> évalué **par datastore** et non par document — le dénominateur excluant les opérations sans
> paramètre porteur de coût. Un ensemble de manifestes desservant le même datastore qui dépasse ce
> plafond est refusé **en tant qu'ensemble**. Le manifeste désigne à cette fin le datastore qu'il
> dessert par un identifiant appartenant à un **ensemble clos déclaré au profil** ; un manifeste
> désignant un identifiant hors de cet ensemble ne compile pas. L'auto-déclaration libre serait
> vaine : deux manifestes inventant chacun leur identifiant échapperaient au plafond d'ensemble et
> rouvriraient exactement l'évasion que ce plafond ferme.

*(B5. Un plafond exprimé en proportion d'un document isolé se contourne de deux façons triviales :
scinder la surface en deux manifestes, ou diluer le dénominateur avec des opérations bornées de
remplissage. R11 n'avait que RC-19 pour atténuation ; une atténuation contournable par découpage
n'en est pas une. La granularité retenue est le **datastore** — celle-là même dont §3.2 (b) et
RC-41 font l'unité de la revendication — et non le « déploiement », notion qu'aucun gate de
publication ne connaît et dont un critère la mentionnant serait inimplémentable.)*

#### M9 — §3.2, clause de périmètre, point (a) (l.254-255)

**Remplacer par :**

> (a) les défauts d'implémentation de l'adaptateur, du moteur, **ou du répartiteur de référence
> lui-même** — la garantie porte sur la **conception** de la surface attestée, jamais sur l'absence
> de défaut dans le code qui la sert ;

#### M10 — §3.2, clause de périmètre, point (d) (l.264-266)

**Ajouter** en fin de point (d) :

> …ainsi que l'**oracle obtenu par répétition d'une opération exposée à faible coût unitaire** :
> une projection minuscule — un identifiant, un booléen, un compte — porte un coût majorant
> minuscule, et le comptage en coût cumulé y autorise un nombre d'appels considérable. L'énumération
> par oracle est bornée en **volume** par I9, elle ne l'est pas en **nombre** sans la déclaration
> optionnelle du §3.4.

#### M11 — §3.2, clause de périmètre, nouveau point (i) (insertion après (h), l.278)

**Insérer avant la phrase de clôture** « QueryMe garantit qu'une surface dont l'accès lui est
exclusif… » :

> (i) le **compromis de la clé de signature de l'évaluateur**. Le répartiteur ne vérifie qu'une
> signature et une correspondance de hachage, et ne voit jamais le manifeste (RC-7) : en aval de
> l'ancre de confiance, la garantie se réduit à cette clé. Son détournement est un contournement
> **total et silencieux** — le journal de RC-15 reconstruit fidèlement le tuple d'une attestation
> forgée, et l'audit croisé de RC-20 est un outil hors ligne opéré par un humain. Aucun chemin de
> détection automatique n'existe et aucun n'est promis.

#### M12 — §3.3, propriété normative 2 (l.329-331)

**Remplacer** « ce qui rend les limites de débit inviolables plutôt qu'approximativement tenues
(RC-38) » **par :**

> ce qui rend les limites de débit **non contournables par construction du comptage, sous les trois
> conditions d'application RC-32, RC-33 et RC-36** — conservatisme de la fenêtre, joignabilité de
> l'état, atomicité inter-répliques — plutôt qu'approximativement tenues (RC-38).

#### M13 — §3.3, section « Émission », proportion maximale (l.349-351)

**Remplacer par :**

> Le profil déclare en outre un **plafond d'opérations budgétées, en proportion et en valeur
> absolue, par datastore**. Le gate de publication refuse l'ensemble des manifestes desservant un
> datastore lorsque leur union dépasse ce plafond : le refus porte sur la conception de la surface,
> non sur une opération, et il n'est pas défait par le découpage du document (RC-19, R11).

#### M14 — §3.3, insertion immédiatement après le paragraphe issu de M13, en fin de la section « Émission », **avant le paragraphe « Exécution. »** (l.353)

**Insérer :**

> **Schéma de signature.** Les trois familles d'artefacts sont signées par un schéma
> **déterministe** — Ed25519 (RFC 8032) ou ECDSA déterministe (RFC 6979). Le motif est de sécurité
> et non d'esthétique : la récupération de clé par nonce ECDSA rejoué est la classe de défaillance
> la plus catastrophique et la plus silencieuse du domaine. La charge utile signée suit un
> **encodage canonique spécifié avec vecteurs de test** : deux encodeurs divergents pour le même
> tuple ouvriraient une surface de substitution au point exact de la décision de confiance.
>
> **Séparation de domaine.** Chaque famille — attestation, autorisation, allocation — porte une
> **étiquette de domaine distincte incluse dans les octets signés**, vérifiée avant tout autre
> champ. Les trois partagent le hachage de manifeste, le profil, l'émetteur et l'expiration : sans
> séparation de domaine, une allocation est vérifiable comme une autorisation, classe d'attaque
> standard sur les enveloppes signées à champs partagés.
>
> **Politique de signature.** Le nombre et les rôles des signataires exigés sont portés par
> **l'ancre de confiance du profil** et vérifiés par le répartiteur — non par le tuple attesté, qui
> reste inchangé. Le schéma est celui de **signatures détachées multiples sur la même charge utile
> canonique**, n-parmi-m déclaré au profil ; un contreseing sur signature créerait un ordre et une
> dépendance sans contrepartie. Sans cela, la « seconde signature selon le palier » du §3.6
> resterait une convention de publication qu'aucun runtime ne peut exiger.

#### M15 — §3.2, artefacts signés, nouvelle sous-section (insertion après l.247)

**Insérer :**

> **Ancre de confiance.** L'autorité de vérification est un **ensemble de clés publiques
> provisionné hors bande** — hors du chemin de livraison de l'artefact, hors de l'atteinte du
> processus de publication, et porteur de la politique de signature du profil. Si la clé de
> vérification voyageait avec le bundle, la signature serait décorative : le répartiteur ne voyant
> jamais le manifeste (RC-7), il n'aurait **aucun autre point de comparaison** et un artefact
> intégralement forgé passerait toutes les vérifications de RC-4.
>
> Les **hiérarchies de clés sont disjointes par profil**. Une clé `dev` est cryptographiquement
> incapable de produire un artefact vérifiable par une ancre `prod` : l'inutilisabilité d'une
> attestation `dev` en `prod` cesse d'être une comparaison de champ au démarrage pour devenir une
> propriété de la vérification de signature, la comparaison subsistant en défense en profondeur.
> Cette séparation n'est pas une précaution générale : la clé `dev` est par conception la plus
> exposée du système — présente sur chaque poste d'auteur et émettant automatiquement à chaque
> compilation (§3.3, §3.6) —, et une clé unique pour les deux profils rendrait fausse
> l'affirmation « aucune fenêtre d'exposition » du §3.6.
>
> **Durée de vie et retrait.** Aucune liste de révocation n'est introduite : une liste est un état
> que le répartiteur devrait consulter, donc une frontière de confiance et une dépendance de
> disponibilité nouvelles — ce que RC-7 et le tripwire du §3.8 lui interdisent par ailleurs. Le
> retrait d'un artefact s'opère donc par **durée de vie courte et non-renouvellement**. Le profil
> déclare une durée de vie maximale par famille d'artefact ; le **délai de retrait effectif est
> cette durée de vie**, et il est publié comme tel. Si une révocation immédiate devenait requise,
> elle rouvrirait §3.10(1) et deviendrait un composant à attester, fail-closed au même titre que
> les compteurs du §3.4 (R13).
>
> **Rotation.** La passe attestante unique (§3.8) interdisant de re-signer un artefact produit par
> une exécution séparée, toute rotation de clé impose la **recompilation et la ré-attestation de
> toute surface déployée** — opération que seul le déterminisme au bit de RC-29 rend sûre, et
> qu'une rotation ratée transforme en panne totale par RC-30. En conséquence : durée de vie de clé
> bornée et déclarée au profil, ancre constituée d'un **ensemble** de clés avec fenêtre de
> recouvrement, et **runbook de rotation d'urgence écrit avant P1** (R19).

#### M16 — §3.4, section « Clé » (l.375-380)

**Remplacer** « Le compteur est clavé par `(identité, sous-clé, relation, axe, fenêtre)` » **par :**

> Le compteur est clavé par `(identité, sous-clé, relation, axe, unité, fenêtre)`, où l'**unité**
> vaut `coût` ou `appels`.

**Et ajouter** en fin de section :

> L'unité `coût` est le régime nominal et reste le seul obligatoire : compter des appels serait
> défait par des appels plus gros. L'unité `appels` existe pour le cas symétrique, que le coût
> cumulé ne borne pas — l'**oracle à faible coût unitaire** (§3.2 (d)) : une opération à projection
> minuscule autorise un nombre d'appels considérable sous un quota de coût intact, ce qui est la
> forme classique de l'énumération par enregistrement.

#### M17 — §3.4, section « Déclaration » (l.412-414)

**Ajouter** en fin de section :

> Une relation **peut** déclarer, en sus de ses deux limites de coût cumulé, une limite en **nombre
> d'appels** par identité et par fenêtre, sur l'un ou l'autre axe. Le profil **doit** pouvoir
> l'exiger ; lorsqu'il l'exige, un manifeste qui l'omet ne compile pas.

#### M18 — §3.4, section « Allocations » (l.472-474)

**Remplacer** « expirante et révocable » **par** « **à durée de vie bornée, non renouvelée au-delà
de la durée maximale déclarée au profil** ».

#### M19 — §3.9, phase P0 (l.734-742)

**Ajouter** à l'énumération des livrables de P0 :

> …ancre de confiance et politique de signature du profil, séparation de domaine des trois
> familles d'artefacts, encodage canonique de la charge utile signée avec ses vecteurs de test, et
> la cohérence du discriminant de cloisonnement le long des chemins de jointure (I1).

*(B4 change le schéma du manifeste et A7 change ce que le répartiteur doit pouvoir exiger. Les deux
déterminent du code P0 et ne peuvent pas être rattrapés après QM-P0-03 et QM-P0-05.)*

#### M20 — §4, régime de garantie non uniforme (l.855-857)

**Remplacer** « six invariants structurels, deux budgétaires, un de débit — ce dernier **appliqué**
et non impossible » **par :**

> cinq invariants structurels, deux budgétaires, deux appliqués — l'auditabilité et le débit, tous
> deux **appliqués** et non impossibles, et le régime structurel lui-même conditionné à deux
> vérifications du répartiteur nommées au §3.2.

#### M21 — §3.2, rappel des trois artefacts signés (l.245)

**Remplacer** « portent sur des quadruplets (identité, sous-clé, relation, axe) » **par** « portent
sur des **quintuplets** (identité, sous-clé, relation, axe, **unité**) ».

*(Le §3.4 dérive le tuple d'allocation de la clé de compteur ; toute composante ajoutée à la clé
doit se propager aux deux endroits, sous peine de rouvrir précisément l'accident que l'argument du
§3.4 ferme.)*

#### M22 — §3.4, section « Allocations », tuple et justification (l.464-470)

**a.** **Remplacer** le tuple par :

> `(hachage du manifeste, identité ou classe d'identité, sous-clé de partition ou joker, relation,
> axe, unité, plafond de fenêtre, profil, émetteur, expiration)`

**b.** Dans la justification qui suit (l.466-467), **remplacer** « Sous-clé **et** axe appartiennent
au tuple parce que le compteur est clavé par `(identité, sous-clé, relation, axe, fenêtre)` »
**par** « Sous-clé, axe **et unité** appartiennent au tuple parce que le compteur est clavé par
`(identité, sous-clé, relation, axe, unité, fenêtre)` ».

**c.** **Ajouter** à cette même justification, après l'argument sur l'axe :

> …et sans l'**unité**, élargir le quota de coût d'un consommateur légitime élargirait du même
> geste son nombre d'appels admissible, ce qui rouvrirait l'oracle à faible coût unitaire que la
> déclaration du §3.4 ferme.

*(Sans (b), l'amendement se contredirait à deux lignes d'écart : un énumérateur binaire pour trois
composantes, et une clé à cinq composantes citée sous un tuple qui en compte six.)*

#### M23 — §3.2, étiquette de l'invariant I9 (l.216)

**Remplacer** l'étiquette *(débit)* **par** *(appliqué)*, par cohérence avec M3, M7 et M20.

#### M24 — §4, permissivité par opération (l.858-859)

**Remplacer** « La permissivité est par opération et non à l'échelle de la surface : un manifeste
massivement budgété est refusé. » **par :**

> La permissivité est par opération et non à l'échelle de la surface : un **ensemble de manifestes
> desservant un même datastore** dont l'union est massivement budgétée est refusé, en proportion
> comme en valeur absolue — le découpage du document n'y change rien.

#### M25 — §3.10(6), volet exécution (l.797-798)

**Remplacer** « Le risque est aujourd'hui **nul en pratique et armé par construction** : il se
réalise à la première contribution externe, c'est-à-dire au moment où le projet réussit. » **par :**

> Ce risque était décrit dans la révision initiale comme « nul en pratique et armé par
> construction ». **L'instruction de §3.10(6) a établi que cette description était fausse** : au
> 2026-08-15, le dépôt était `visibility: public` et `allow_forking: true`, la CI se déclenchait
> sur `pull_request`, aucun de ses jobs ne portait la garde de fork exigée par ADR 018 Orion §3.1,
> une PR de fork contrôlant en outre son propre `runs-on` et pouvant viser le pool statique
> partagé ; `main` n'était par ailleurs ni protégée ni couverte par un ruleset. Le risque n'était
> donc ni futur ni théorique. **Les deux défauts ont connu deux sorts distincts, et ils sont
> nommés séparément.**
>
> *Garde de fork — corrigée.* La garde exigée par ADR 018 Orion §3.1 est posée sur l'ensemble des
> jobs de la CI (PR #10) ; le veto correspondant est **levé par correction**.
>
> *Protection de `main` — non corrigée ; veto levé par acceptation de risque écrite.* La correction
> est structurellement impossible pour le fleet — aucune App du dispositif ne détient
> `Administration` au niveau dépôt — et le porteur, seul détenteur du droit, l'a refusée le
> 2026-08-15. Le risque est inscrit au §5 sous **R23** et coté **moyen** : son exploitation suppose
> un droit d'écriture déjà détenu, là où l'absence de garde de fork était atteignable depuis
> Internet. L'acceptation est **conditionnée** au contrôle résiduel qu'énonce R23 — épinglage des
> six consommateurs par SHA de commit en lockfile — et porte ses propres motifs de réexamen.
>
> §3.10(6) est en conséquence **instruite et rendue en totalité** : clearance sur le volet
> exposition ; sur le volet exécution, deux vetos, l'un levé par correction, l'autre par
> acceptation écrite **et conditionnée** (R23). La surface conserve deux travaux de suite, non un
> veto : la **politique de divulgation** appelée par R22, et la vérification du contrôle résiduel
> de R23 (#11, `QM-P0-07`).

#### M26 — §3.4, section « État » (l.480-486)

**Ajouter** en fin de section :

> Le **puits d'attribution** d'I8 suit le même régime que l'état de comptage : colocalisé dans
> PostgreSQL par défaut, écrit dans la transaction qui porte l'appel ; externe seulement si le
> profil le déclare, sous la même clearance §3.10(10), avec la même conséquence sur la
> disponibilité de la surface (R21).

#### M27 — §3.10, surface (1) (l.773-774)

**Remplacer** « modèle de signature et de révocation des attestations, autorisations et allocations
— custody, rotation, et déterminisme ou non de la signature, dont dépend la portée exacte de
RC-3 ; » **par :**

> modèle de signature et de révocation des attestations, autorisations et allocations — custody et
> rotation. **Surface instruite et rendue** (thread `bastion-adr-001-invariants`) : le schéma de
> signature est déterministe et son encodage canonique spécifié (§3.3), l'ancre de confiance et le
> régime de retrait par durée de vie bornée sont fixés (§3.2), et la portée de RC-3 n'est plus
> suspendue à cette question — elle couvre désormais l'artefact signé entier. Ce qui demeure de la
> surface est **opérationnel** et non doctrinal : custody des clés et exécution de la rotation
> (R19, RC-46) ;

*(Même motif que M25, et deuxième saisine de Vigil sur ce point : laisser une question tranchée
présentée comme ouverte est la symétrique exacte de laisser une affirmation fausse debout. Un
amendement dont le motif est l'exactitude ne peut pas s'en dispenser au seul endroit où c'est
lui-même qui a tranché.)*

#### M28 — §3.10, surface (4) (l.778-780)

**Remplacer** « **solidité du jeu d'invariants lui-même** au regard de la revendication portée, y
compris la validité de la partition ternaire du §3.2 et la légitimité de présenter I9 comme une
garantie *appliquée* ; » **par :**

> **solidité du jeu d'invariants lui-même** au regard de la revendication portée. **Surface
> instruite et rendue** (thread `bastion-adr-001-invariants`) : le veto porté sur cette surface est
> **levé par le présent amendement**, qui en corrige la cause. La partition ternaire du §3.2 est
> rectifiée — régime **structurel** {I1 sous réserve, I2, I4, I5, I6}, **budgétaire** {I3, I7},
> **appliqué** {I8, I9} (M1, M3, M7, M23) —, l'étiquetage se fait désormais par couple obligation /
> dépassement (M4), et la revendication du §4 est alignée sur cette partition (M20). Présenter I9 —
> et I8 avec lui — comme *appliqués* n'est plus une question ouverte mais le régime retenu, avec sa
> conséquence assumée sur la disponibilité (R21). Ce qui demeure de la surface est **opérationnel**
> et non doctrinal : matérialisation du noyau d'invariants et de son étiquetage (#9, `QM-P0-02`) et
> traçabilité du verdict par son thread (#12, RC-26) ;

*(Même motif que M25 et M27, et troisième saisine de Vigil sur ce point. §3.10(4) est la surface que
le présent amendement traite le plus directement : la laisser présentée comme « à instruire » alors
que ses trois questions — partition, statut d'I9, solidité du jeu — sont tranchées ici même serait
la contradiction la plus visible du texte.)*

### A3. Critères de résolution

#### Critères modifiés

- **RC-3** — étendu : la comparaison porte sur l'**artefact signé entier**, encodage canonique
  compris, et non sur la seule charge utile. *Vérif :* comparaison binaire en CI, plus les vecteurs
  de test de l'encodage canonique. Cette extension supprime le besoin d'un outil capable de
  déballer l'enveloppe — nouvelle surface d'analyse là où le §3.8 en interdit partout ailleurs. Le
  schéma de signature étant déterministe (M14), la réserve antérieure « le déterminisme de la
  signature n'est pas présumé » est **levée** (M27).
- **RC-4** — **sixième cas** : le répartiteur refuse de démarrer si l'attestation est valablement
  formée mais **signée par une clé hors de l'ancre de confiance du profil**. *Vérif :* six tests,
  un par cas, celui-ci mené avec une clé attaquante bien formée.
- **RC-5** — étendu : une attestation de profil `dev` présentée à un runtime `prod` est rejetée **à
  la vérification de signature, avant même la comparaison de profil**. *Vérif :* deux tests — l'un
  prouvant le rejet cryptographique, l'autre prouvant que la comparaison de champ subsiste en
  défense en profondeur.
- **RC-19** — étendu : le gate refuse un ensemble de manifestes desservant le même datastore dont
  l'union dépasse le plafond, en proportion **et** en valeur absolue, le dénominateur excluant les
  opérations sans paramètre porteur de coût. *Vérif :* quatre tests de rejet — (a) dépassement en
  proportion ; (b) dépassement en valeur absolue ; (c) **découpage** : deux manifestes désignant le
  **même** identifiant de datastore, individuellement conformes, dont l'union ne l'est pas ;
  (d) **identifiant hors ensemble clos** : un manifeste désignant un datastore absent de l'ensemble
  déclaré au profil ne compile pas — c'est ce qui empêche l'évasion par invention d'identifiant.
- **RC-26** — précisé : chaque verdict identifie **l'instance qui l'a rendu** — rôle **et** thread
  —, et non le seul rôle. *Vérif :* les dix verdicts tracés portent chacun leur thread, et deux
  verdicts rendus par deux instances d'un même rôle restent distinguables. *(Motif : trois surfaces
  sont aujourd'hui instruites par deux instances Bastion distinctes —
  `bastion-adr-001-invariants` pour (1) et (4), `bastion-audit-post-merge` pour (6). « Dix verdicts
  tracés » ne disait pas par qui, ce qui rendait la traçabilité satisfaite en apparence.)*
- **RC-40** — nouveau point **(e)** : le patch de sécurité `v0.2.3` de la ligne `0.x` borne
  l'**appel unitaire** sans casser aucun consommateur qui déclare sa borne. Contenu normatif du
  patch : `limit` devient un champ **requis** — `int`, et non `int | None` — borné à
  `[0, MAX_LIMIT]` avec `MAX_LIMIT = 1000`. Deux verrous distincts, à ne pas confondre :
  1. **Côté descripteur**, la validation Pydantic rejette l'absence de `limit` par une
     `ValidationError` de code `missing`, et une valeur hors borne par une `ValidationError` de
     code `less_than_equal` ou `greater_than_equal`. Ce sont les codes standard de Pydantic ;
     aucun code applicatif n'y intervient.
  2. **Côté compilateur**, la borne est **re-vérifiée avant émission SQL** et son dépassement lève
     une `CompilationError` portant un `ValidationIssue` de code applicatif
     `limit_out_of_bounds`. Ce second verrou est le dernier rempart contre un descripteur
     construit en contournant la validation — `model_construct()` notamment.

  Il n'existe aucun défaut silencieux, en aucun point : un appel qui ne déclare pas sa borne est
  rejeté, jamais complété. Une troncature muette aurait été le pire mode d'échec possible sur un
  patch poussé à six consommateurs vivants, l'appelant recevant un résultat partiel qu'il croit
  complet.

  **Portée, à ne jamais élargir en communication :** *B9 atténué en `v0.2.3` — l'appel unitaire est
  borné ; l'extraction cumulée ne l'est pas, et ne le sera, pour un consommateur donné, qu'à son
  **passage sur une surface attestée où I9 s'applique** — ce qui suppose la sortie de la ligne
  `0.x` : P2 pour Blue, P5 pour les cinq autres. I9 (P1) en est la précondition, non l'échéance.*
  Cette formulation est celle qui engage publiquement (RC-24) ; les trois échéances de R20 en sont
  le détail opposable. La condition porte sur l'**arrivée** et non sur le départ : quitter `0.x`
  vers un chemin non attesté satisferait une condition de sortie sans borner quoi que ce soit.

  *Vérif :* (i) `v0.2.2` désigne le même objet git qu'avant (RC-40 (a)) ; (ii) le diff
  `v0.2.2`→`v0.2.3` est limité à la borne d'extraction et à ses gardes ; (iii) **les suites de
  tests des six consommateurs sont vertes sous `v0.2.3`**, après les modifications de code que
  l'obligation de `limit` impose — preuve de non-régression démontrée par leurs tests, non
  déclarée ; (iv) trois tests prouvent respectivement le rejet d'un `limit` absent, le rejet d'un
  `limit` hors borne, et l'absence de toute troncature ; (v) un test prouve que la garde du
  compilateur rejette une valeur hors borne construite en contournant la validation ; (vi)
  l'inventaire des appels existants sans borne ou au-delà de `MAX_LIMIT` est produit, avec le
  traitement retenu pour chacun.

#### Critères nouveaux

- **RC-12bis (I1)** — un manifeste joignant deux relations à discriminants de cloisonnement
  hétérogènes non reliés par une relation de dérivation déclarée et cloisonnée est **rejeté**.
  *Vérif :* test de rejet ; et, sur le corpus, aucune **opération de jointure** ne restitue une
  ligne d'un tenant autre que celui de l'identité appelante. Ce cas figure parmi les dix
  configurations vulnérables de RC-2.
- **RC-33bis (I8)** — l'indisponibilité du puits d'attribution entraîne le refus des opérations.
  *Vérif :* injection de faute sur le **chemin d'écriture du journal**, le reste du service restant
  opérant — aucune opération n'est alors servie non attribuée. Même moyen de vérification que
  RC-33, et pour le même motif : rendre la base injoignable rendrait le critère vide. Lorsqu'un
  profil autorise un puits externe, le même critère s'exerce en rendant ce puits injoignable.
- **RC-42** — la séparation de domaine est effective. *Vérif :* **six** tests de rejet croisé, une
  paire ordonnée par couple de familles — un artefact d'une famille présenté à la vérification
  d'une autre est rejeté sur l'étiquette de domaine, avant toute autre vérification de champ.
- **RC-43** — aucun artefact n'est exploitable au-delà de la durée de vie maximale déclarée au
  profil pour sa famille. *Vérif :* test d'expiration par famille ; et **absence de toute
  bibliothèque ou de tout chemin de consultation de liste de révocation** dans l'arbre de
  dépendances du répartiteur — même moyen que RC-7, et pour la même raison.
- **RC-44** — le répartiteur **exige** la politique de signature portée par l'ancre du profil.
  *Vérif :* une attestation `prod` mono-signée est rejetée par un runtime dont l'ancre déclare
  n-parmi-m avec n > 1 ; et le rejet survient avant la vérification des hachages.
- **RC-45** — lorsqu'un profil exige la limite en nombre d'appels, un manifeste qui l'omet ne
  compile pas ; et une identité atteignant cette limite est refusée alors même que son quota de
  coût reste intact. *Vérif :* test de rejet à la compilation ; scénario d'oracle mené jusqu'au
  refus, quota de coût constaté non épuisé ; et **une allocation portant l'unité `coût` n'élargit
  pas la limite en nombre d'appels** — test dédié, qui exerce la propagation de M21 et M22.
- **RC-46** — la rotation de clé est exécutable sans perte de service. *Vérif :* runbook écrit et
  **rejoué** avant l'ouverture de P1 sur une surface de référence — ré-attestation de flotte
  produisant, par RC-29, un artefact identique octet pour octet ; fenêtre de recouvrement de
  l'ancre prouvée par une vérification réussie sous l'ancienne et la nouvelle clé.

### A4. Risques ajoutés au §5

- **R19 — Coût de la rotation de clé.** La passe attestante unique (§3.8) fait de toute rotation
  une recompilation et une ré-attestation de **toute la flotte**, qu'une rotation ratée transforme
  en panne totale par RC-30. C'est précisément ce coût qui pousse une organisation à ne jamais
  tourner une clé — le mode d'échec est l'immobilisme, pas l'accident. Atténuation : RC-29
  (déterminisme au bit, qui rend la ré-attestation sûre), ancre multi-clés à fenêtre de
  recouvrement, runbook écrit et rejoué avant P1 (RC-46).
- **R20 — Extraction cumulée non bornée sur la ligne `0.x`.**
  `src/queryme/descriptor.py:163` @ `17e78871d` : `limit: int | None = Field(default=None, ge=0)`
  — borne basse seulement, et `None` faisait que `compiler.py:173-174` n'émettait **aucune** clause
  `LIMIT`. Il ne s'agissait pas d'un plafond trop haut mais d'une absence totale de plafond, sur une
  surface servant six services de production, pendant que le §3.3 présente ce plafond comme *la*
  protection contre l'exfiltration.

  **Décision du porteur, 2026-08-15 : correction, non acceptation de risque.** `v0.2.3` (PR #13,
  squash `a08a0a4b`) rend `limit` requis et borné à `[0, 1000]`, sans déplacer `v0.2.2`
  (RC-40 (a)) et sans porter aucun contenu de la nouvelle ligne.

  **Le patch est une rupture de contrat, non un simple bump de version.** `limit` devenant requis,
  tout consommateur qui construisait un `QueryDescriptor` sans le déclarer cesse de valider :
  chaque migration exige une **modification de code réelle** chez le consommateur, et non un
  déplacement d'épinglage. Le coût est nommé ici parce que le présenter comme un bump ferait
  sous-estimer la durée pendant laquelle le risque reste courant — même discipline d'honnêteté que
  la clause anti-survente du §3.2.

  **La correction est partielle, et elle est nommée comme telle.** Elle borne l'**appel unitaire** ;
  elle ne borne pas l'**extraction cumulée**. `offset` demeure non borné : extraire une relation
  entière coûte désormais `ceil(lignes / 1000)` appels parfaitement nominaux, sans friction et sans
  autorisation — c'est-à-dire exactement le chemin que le driver 10 et le §3.4 décrivent comme
  principal.

  Borner le cumul relève d'I9, donc de **P1** : hors du périmètre d'un patch de la ligne `0.x`. P1
  est cependant une **précondition de fermeture, non la fermeture** — il livre I9 dans la nouvelle
  ligne et ne modifie pas `queryme 0.x`, dont un consommateur épinglé garde `offset` non borné
  après P1. Le résidu se ferme **par consommateur et par datastore**, lorsque celui-ci **arrive sur
  une surface attestée où I9 s'applique** — ce qui suppose sa sortie de la ligne `0.x` : **P2**
  pour Blue, **P5** pour les cinq autres (§3.9 P5 — « migration des six consommateurs hors de la
  bibliothèque de validation par liste blanche, datastore par datastore »). La condition porte sur
  l'arrivée et non sur le départ : quitter `0.x` vers un chemin non attesté ne borne rien.
  L'exclusivité d'accès du §3.2 (b) et RC-41 en sont la conséquence, non le contenu.

  **Trois échéances distinctes, aucune interchangeable :**
  1. **Risque courant — appel unitaire non borné.** Cesse *par consommateur*, lorsque celui-ci a
     migré vers `v0.2.3`, migration entendue comme la modification de code que l'obligation de
     `limit` impose, et non comme le seul déplacement de l'épinglage.
  2. **Risque résiduel — extraction cumulée par pagination répétée.** Cesse *par consommateur et
     par datastore*, à son arrivée sur une surface attestée où I9 s'applique, donc à la sortie
     complète de la ligne `0.x` : **P2** pour Blue, **P5** pour les cinq autres. P1 n'en est que la
     précondition.
  3. **Revendication du §3.2.** S'acquiert par datastore une fois (1) et (2) tenues (RC-41).

  Tant que (2) n'est pas tenue pour un datastore donné, aucune communication publique ni aucun
  critère ne présente B9 comme résolu pour celui-ci.
- **R21 — Disponibilité du puits d'attribution.** Le fail-closed d'I8 (M7) transforme une panne du
  puits d'attribution en indisponibilité de la surface, exactement comme R13 le fait pour l'état de
  comptage — et il **double** cette surface de déni de service plutôt que de la partager. La
  colocalisation par défaut dans PostgreSQL (M26) ramène le risque au niveau de R13 : le puits ne
  tombe que si la base tombe. Un puits externe autorisé par un profil le réintroduit intégralement
  et déplace une part de la disponibilité de la surface sur un second composant. La variante
  fail-open est exclue : elle détruirait I8 en le rendant contournable par saturation du journal.
- **R22 — Divulgation prématurée du défaut de borne `0.x`.** Le 2026-08-15, une PR sur le dépôt
  public `ZabLaboratory/QueryMe` (commit `d2b5520`, PR #13) a publié une description du défaut de
  borne d'extraction de la ligne `0.x` nommant les six services consommateurs, avant qu'aucun ne
  soit migré. Le défaut lui-même était déjà lisible dans le code public depuis avril 2026 ; ce qui
  a été ajouté est la liste des services affectés et la confirmation de l'exploitabilité. Le
  contenu reste visible dans l'historique de la PR ; ni la réécriture d'historique ni une demande
  de purge au support ne le retirent réellement, et aucun secret n'est en cause — **le risque est
  donc accepté**. Sa contrepartie est temporelle : l'exposition dure tant que les consommateurs
  restent sur `v0.2.2`. La migration des six services est ouverte comme unité datée, et son urgence
  est fixée par une question préalable — une entrée non fiable atteint-elle la construction d'un
  `QueryDescriptor` dans l'un d'eux. Atténuation permanente : `.gitignore` sur les fichiers de
  protocole d'agent (PR #13) et politique de divulgation à définir en §3.10(6).
- **R23 — `main` sans protection de branche ni ruleset.** Au 2026-08-15,
  `ZabLaboratory/QueryMe` présente `main` en `protected: false` et `rulesets: []` (vérifié par API,
  non supposé) : aucun check requis avant merge, aucune review requise, aucune application de
  CODEOWNERS, aucune interdiction de force-push, aucune protection de tag. Bastion avait posé veto
  sur cette surface et refusé d'en écrire l'acceptation ; le veto est levé ici par acceptation
  explicite, sur deux fondements. D'une part la correction est **structurellement impossible pour
  le fleet** : aucune App du dispositif ne détient le droit `Administration` au niveau dépôt,
  seulement au niveau organisation, et Keeper a été bloqué à l'application. D'autre part le
  porteur, seul détenteur du droit, a refusé explicitement le 2026-08-15 tant l'application
  manuelle que l'extension de scope d'une App par la chaîne doctrine.

  Bastion révise à cette occasion sa propre cotation, d'**élevée à moyenne**. La cotation initiale
  rangeait ce risque aux côtés de l'absence de garde fork ; c'était une erreur d'analyse. L'absence
  de garde fork était atteignable depuis Internet ; l'absence de protection de branche ne l'est
  pas : son exploitation suppose de détenir **déjà** un droit d'écriture. Ce n'est pas un contrôle
  de périmètre mais un contrôle d'intégrité et de rayon d'explosion — il ne repousse aucun
  attaquant externe, il limite les conséquences d'une App compromise, d'un agent défaillant ou
  d'une erreur humaine.

  Ce qui est accepté, précisément : (a) un commit peut atteindre `main` sans qu'aucun des sept jobs
  de CI n'ait eu à passer, ce qui rend purement déclaratoire la règle « pas de merge en CI rouge »
  de `docs/rules/git.md` ; (b) `main` peut être force-pushée et son historique détruit ;
  (c) l'absence de protection de tag rend les tags de version **mutables** — un tag `vX.Y.Z` réémis
  modifierait le code servi à tout consommateur qui le résout dynamiquement.

  Le contrôle résiduel qui rendrait cette acceptation pleinement tenable est l'épinglage effectif
  de chacun des six consommateurs sur une révision verrouillée et opposable, et non sur une
  référence flottante. **Cette vérification n'a pas été faite ici et elle conditionne
  l'acceptation.** Elle n'est en particulier **pas** satisfaite par le constat que les six
  résolvent aujourd'hui la même révision : le tag `v0.2.2` déréférence vers
  `b80e5b29e2c5bfe6864c1cc5d98f752ed0af7154`, de sorte qu'un consommateur flottant sur le tag et un
  consommateur verrouillé par SHA produisent une observation identique. Le fait ne discrimine pas
  et ne vaut pas preuve.

  Le critère discriminant s'établit pour chaque consommateur pris nommément : (i) la référence
  déclarée dans son `pyproject.toml` ; (ii) la révision effectivement verrouillée dans son
  `uv.lock` committé ; (iii) l'**opposabilité** de ce verrou — la CI du consommateur installe-t-elle
  en mode verrouillé (`uv sync --frozen`, `uv lock --check`), de sorte qu'une régénération
  silencieuse échoue au lieu de passer. Un verrou qui n'est pas opposable en CI n'est pas un
  contrôle, c'est une convention.

  Cette vérification est portée par l'issue **#11 (`QM-P0-07`)**, inventaire des six épinglages,
  dont elle devient un critère de résolution explicite service par service selon les trois points
  ci-dessus. Tant qu'elle n'est pas rendue, la présente acceptation demeure **conditionnelle** : le
  tag `v0.2.2` reste mutable faute de protection de tag, et un seul consommateur flottant convertit
  le point (c) du paragraphe précédent en chemin de compromission de la chaîne d'approvisionnement
  de production. Une réponse négative de #11 sur un seul service, le passage d'un consommateur à
  une référence flottante, ou toute réémission du tag `v0.2.2` périment cette acceptation sans
  autre formalité et rouvrent le veto.

  Réexamen obligatoire à la première occurrence de l'un de ces événements : premier contributeur ou
  adoptant externe (RC-39, P4) ; attribution d'un droit d'écriture à une identité humaine ou
  applicative supplémentaire ; obtention par une App du droit `Administration` au niveau dépôt, qui
  reconvertit le risque en simple correction et périme l'acceptation.

### A5. Ce que cet amendement ne tranche pas

- **Trois** des dix surfaces de §3.10 sont instruites — (1) et (4) par
  `bastion-adr-001-invariants`, (6) par `bastion-audit-post-merge` — et **sept** ne le sont pas :
  (2), (3), (5), (7), (8), (9), (10). RC-26 en attend dix, chacun tracé avec son thread.
- §3.10(1) est **rendue** au plan doctrinal (M27) ; ce qui en subsiste est opérationnel — custody
  des clés et exécution de la rotation, portées par R19 et RC-46.
- §3.10(4) est **rendue** au plan doctrinal (M28) : la partition ternaire est corrigée et le veto
  levé par cet amendement ; ce qui en subsiste est opérationnel — matérialisation du noyau
  d'invariants (#9, `QM-P0-02`) et traçabilité du verdict par thread (#12, RC-26).
- §3.10(6) est **rendue en totalité** : clearance sur le volet exposition (historique complet,
  quatorze commits sur toutes les refs, cinquante-six blobs, deux scanners, zéro finding) ; deux
  vetos sur le volet exécution — garde de fork absente et `main` non protégée —, levés par des
  voies **différentes** : le premier par correction (PR #10), le second par **acceptation de risque
  écrite** (R23, M25), la correction étant hors de portée du fleet et refusée par le porteur.
  Aucun veto ne reste ouvert. La surface conserve deux travaux de suite : la **politique de
  divulgation** appelée par R22, et la vérification du contrôle résiduel dont R23 fait dépendre son
  acceptation — pour chacun des six consommateurs, référence déclarée, révision verrouillée et
  opposabilité du verrou en CI, portée par l'issue **#11** (`QM-P0-07`). Cette vérification n'est
  pas faite ici ; R23 énonce ce qu'il advient si elle échoue.
- Les risques résiduels non corrigeables relevés par l'instruction — publication, dans le message
  de merge public `9d0541d`, du nom de l'orchestrateur de runners, de la règle de matching des
  labels, de l'existence d'un pool statique partagé et du nom d'une autre structure étage 1 ; et
  publication depuis `a96decb` du schéma réel de ZabTruth dans `tests/test_compiler.py:26-60` —
  sont **actés comme résiduels** : réécrire l'historique d'un dépôt épinglé par six services coûte
  plus qu'il ne rend.
- L'urgence de migration des six consommateurs n'est pas fixée ici : R22 la pose comme unité datée
  dont la priorité dépend d'une question préalable, et aucune formulation du présent amendement ne
  suppose l'une ou l'autre réponse.

### A6. Incidence sur les issues déjà ouvertes

- **#8** (`QM-P0-01`, schéma du manifeste) — périmètre étendu : identifiant de datastore desservi,
  **pris dans un ensemble clos déclaré au profil** (M8) ; déclaration optionnelle de limite en
  nombre d'appels (M17) ; relation de dérivation entre discriminants de cloisonnement (M6).
  Critères d'acceptation à compléter en conséquence.
- **#9** (`QM-P0-02`, noyau d'invariants) — la partition change : structurel {I1 sous réserve, I2,
  I4, I5, I6}, budgétaire {I3, I7}, appliqué {I8, I9} ; étiquetage par couple obligation /
  dépassement (M4, M23) ; vérificateur de cohérence du discriminant le long des jointures
  (RC-12bis).
- **#11** (`QM-P0-07`, trajectoire de version) — périmètre étendu : `v0.2.3` est une publication sur
  la ligne gelée, à distinguer explicitement de la nouvelle ligne ; RC-40 (e) s'ajoute à ses
  critères. L'inventaire des six épinglages devient le support de suivi des trois échéances de R20
  et de R22, et il porte pour chaque consommateur **la modification de code exigée par l'obligation
  de `limit`** — la migration vers `v0.2.3` n'est pas un déplacement d'épinglage et ne doit pas
  être suivie comme tel. Il porte désormais aussi le critère de résolution posé par **R23** : pour
  chaque consommateur, référence déclarée, révision verrouillée, opposabilité du verrou en CI.
- **#12** (`QM-P0-10`, gate §3.10) — surfaces (1) et (4) : **VETO levé** par cet amendement ;
  surface (6) : **clearance sur l'exposition ; sur l'exécution, deux vetos, l'un levé par
  correction (garde de fork, PR #10), l'autre par acceptation de risque écrite et conditionnée
  (R23, `main` non protégée)** — aucun veto n'y reste ouvert ; en suite, la politique de divulgation
  et la vérification du contrôle résiduel de R23 (#11). Les sept autres surfaces restent sans
  verdict. Chaque ligne du tableau porte désormais le **thread** qui a rendu le verdict, et non le
  seul rôle (RC-26).
- **QM-P0-03** (formats) et **QM-P0-05** (évaluateur), non encore créées — corps à reprendre avant
  création : ancre de confiance hors bande et son format, séparation de domaine, encodage canonique
  à vecteurs de test, politique de signature n-parmi-m portée par l'ancre, durée de vie par
  famille, composante `unité` du tuple d'allocation.
- **QM-P0-04** (modèle de profil), non encore créée — corps à reprendre : ensemble clos des
  identifiants de datastore, durées de vie maximales par famille d'artefact, exigibilité de la
  limite en nombre d'appels, admissibilité d'un puits d'attribution externe.
- **QM-P0-06** (corpus) — la configuration vulnérable « jointure à discriminants hétérogènes »
  entre dans les dix (RC-12bis).
- **QM-P0-08** (clause publique verbatim) — le texte à publier est celui issu de M9, M10 et M11 ;
  sa rédaction était suspendue à ce verdict, elle est débloquée par cet amendement.
