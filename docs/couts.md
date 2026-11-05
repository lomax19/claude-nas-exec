# Coûts — de l'exec-api à Friday

> État au 27/10/2026. Les montants viennent du compte ElevenLabs (lu par son API), de l'abonnement Claude du NAS (lu dans la configuration de Claude Code) et des grilles de prix publiques citées en bas de page.
> Les prix en dollars sont convertis à environ 0,87 € pour 1 $. Le montant réellement débité dépend de la banque.

## Ce que ça coûte chaque mois

| Poste | À quoi ça sert | Formule | Coût |
|---|---|---|---|
| **Claude Pro** (Anthropic) | Le cerveau : chat Claude, Claude Code « Freeplex NAS », Friday, Freeplex Web | Abonnement Pro (vérifié : `subscriptionType: pro`) | **≈ 21 € / mois TTC** (20 $ HT ; 22 € via l'App Store ; ≈ 17,50 € / mois en annuel) |
| **ElevenLabs Starter** | Voix d'Aurore : réponses de Friday et résumés vocaux de Claude | Starter, payé à l'année depuis le 24/10/2026 | **≈ 5 $ / mois** (≈ 60 $ / an, soit ≈ 52 € / an, TVA éventuelle en plus) |
| **Total récurrent** | | | **≈ 26 € / mois** |

## Dépenses ponctuelles

| Date | Dépense | Montant |
|---|---|---|
| 21/10/2026 | Crédits ElevenLabs (avant l'abonnement), rechargement automatique désactivé | 5 $ (≈ 4,40 €) |
| 24/10/2026 | Micro USB pour le PC (dictée à Friday) | montant non noté |

## Ce qui est gratuit

| Élément | Rôle |
|---|---|
| exec-api, nas-vocal, Friday (`server.py`, page, outils) | Code maison, tourne sur le NAS |
| Docker, nginx, certbot (Let's Encrypt) | Hébergement et HTTPS du domaine public |
| Nom de domaine dynamique `ton-domaine.example` | Adresse publique du NAS |
| Tailscale (formule personnelle) | Réseau privé : HTTPS de Friday, SSH de secours, accès au téléphone |
| Home Assistant, Whisper (dictée), Piper (voix de secours) | Domotique et voix locales |
| MemPalace | Mémoire de Claude |
| Termux | Résumés vocaux joués sur le téléphone |

## Ce qui n'est pas compté ici

- **Le NAS, les disques, la Freebox et l'électricité** : ils existaient déjà pour Plex et la domotique. Friday ne fait que les utiliser.
- **L'ancienne clé API Anthropic** (facturation à l'usage, utilisée jusqu'au 04/10/2026) : remplacée par l'abonnement Pro. Montants passés non retrouvés.

## Consommation à surveiller

| Ressource | État au 27/10/2026 | Ce qui la consomme |
|---|---|---|
| Crédits ElevenLabs | **25 927 restants sur 36 932**, remise à zéro le 24/11/2026 | ≈ 400 à 850 caractères par résumé vocal long ; les réponses courtes de Friday coûtent peu |
| Quota hebdomadaire Claude Pro | variable (bandeau de l'appli) | Friday est le plus gros consommateur. L'aiguilleur Haiku/Sonnet/Opus et la réduction des connecteurs (27/10) servent à l'économiser |

Une alerte vocale + Telegram prévient quand il reste moins de 5 000 crédits ElevenLabs (`veille-cyber/credits.py`).

## Sources des prix

- Claude Pro : [aide Anthropic — prix de Claude Pro](https://support.anthropic.com/en/articles/8325610-how-much-does-claude-pro-cost) (20 $ / mois + taxes) ; [GeoToolbox — prix de Claude en euros, mis à jour le 25/10/2026](https://geotoolbox.ai/fr/blog/prix-claude) (≈ 21 € TTC par mois sur le web, 22 € sur l'App Store).
- ElevenLabs Starter : [Hackceleration — prix ElevenLabs, juin 2026](https://www.hackceleration.com/labs/elevenlabs-pricing) (6 $ / mois en mensuel, ≈ 5 $ / mois en annuel, 30 000 crédits par mois).
