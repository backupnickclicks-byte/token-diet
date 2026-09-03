# token-diet

Taglia il consumo di token di Claude Code del **30–52%**, con enforcement
meccanico (non "buoni propositi") e misurazione sui tuoi transcript reali.

Non è un plugin basato su stime teoriche: ogni soglia qui dentro nasce
dall'analisi di sessioni vere.

---

## L'idea che conta

**I token si pagano per turno, non per lettura.** Tutto ciò che entra nel
contesto viene rimandato al modello a *ogni* turno successivo (`cache_read`).
Un dump da 20k token al turno 10 di una sessione da 200 turni non costa 20k:
costa 20k × 190.

Quindi l'obiettivo non è "leggere meno una volta". È **tenere piccolo il
contesto residente**.

### Cosa è emerso dai dati reali (20 sessioni, 6.571 turni)

| Voce | Misura |
|---|---|
| Overhead fisso (system prompt + schemi tool MCP) | **56.770 token**, presenti prima che tu scriva una parola |
| Quota dell'overhead sul contesto medio per turno | **58%** |
| Costo totale del solo overhead sui turni misurati | **373 milioni di token** |
| `Read` senza `offset`/`limit` | **100%** |
| Screenshot a piena risoluzione | **242 su 338** |

L'overhead fisso è la leva più grande, e nessuno la guarda mai.

---

## Installazione

```bash
git clone <url-del-tuo-repo> token-diet
cd token-diet
./install.sh
```

L'installer fa un backup con timestamp di `~/.claude/settings.json`, registra
l'hook senza toccare le altre chiavi, scrive `~/.claude/token-diet.json` con le
soglie di default ed esegue la suite di self-test (19 casi). Se il self-test
fallisce, l'installazione si ferma.

**Riavvia Claude Code**: gli hook si caricano all'avvio della sessione.

Disinstallazione pulita: `./install.sh --uninstall`

---

## Uso

```bash
/token-doctor     # overhead fisso + server MCP caricati ma mai usati
/tokens           # report completo: dove finiscono i token
```

Da terminale:

```bash
python3 scripts/tokens.py doctor          # la leva più grande
python3 scripts/tokens.py report --last 10
python3 scripts/tokens.py estimate        # risparmio proiettato sui tuoi dati
python3 scripts/tokens.py savings         # cosa ha bloccato l'hook finora
python3 scripts/tokens.py baseline        # un numero, per il confronto prima/dopo

python3 scripts/outline.py FILE           # mappa del file: ~5% del costo di una lettura
python3 scripts/outline.py --dir src      # mappa del repo
```

---

## Come ottiene il risparmio

### 1. Overhead fisso — la leva grande (`/token-doctor`)
Ogni server MCP e ogni plugin connesso carica i suoi schemi in **ogni turno**,
che tu lo chiami o no. Il doctor ti dice quali hai abilitato e mai usato.
Disabilitare quelli inutili in un progetto non costa nessuna capacità: li
riattivi quando servono. Da solo vale il 30–40%.

### 2. Guard hook — enforcement meccanico
`scripts/guard.py` gira come `PreToolUse` e **blocca i payload sovradimensionati
prima che entrino nel contesto**, restituendo il comando delimitato da usare al
posto suo:

| Blocca | Propone |
|---|---|
| `npm install`, build, test senza limite | `2>&1 \| tail -n 30` |
| `git log` / `git diff` non delimitati | `--oneline -n 20`, `--stat` |
| `cat` di file > 60 KB | `outline.py` poi `sed -n '120,200p'` |
| `rg -r` senza `-l` | prima i file, poi le righe |
| `Read` di file grandi senza range | outline + `offset`/`limit` |
| Rilettura dello stesso file immutato | "è già nel contesto" |
| Screenshot senza `scale` | `"scale": 0.5` → un quarto dei token |

È volutamente chirurgico: lascia passare tutto ciò che è già delimitato.
Usa il contratto exit-code-2 (supportato da ogni versione di Claude Code),
quindi degrada in sicurezza invece di fallire in silenzio.

### 3. `outline.py` — struttura invece di contenuto
Verificato su file reali: **13–24× più economico** di una lettura completa.
Ti dà simboli e numeri di riga, abbastanza per decidere le 40 righe che servono.

### 4. Skill + `CLAUDE.md.snippet`
La disciplina lato modello: cerca prima di leggere, delimita l'output, non
rileggere, sessione nuova invece di compattazione.

---

## Configurazione

`~/.claude/token-diet.json`:

```json
{
  "enabled": true,
  "max_read_bytes": 60000,
  "max_image_bytes": 400000,
  "cat_max_bytes": 60000,
  "screenshot_teach_once": true,
  "block_bash": true,
  "block_read": true,
  "block_reread": true,
  "block_screenshots": true,
  "allow_paths": []
}
```

- `allow_paths`: sottostringhe di path che bypassano ogni controllo.
- Bypass per un singolo comando: `TOKEN_DIET=off`.
- Spegnere tutto: `"enabled": false`.

Se un controllo dà fastidio, alza la soglia — non disinstallare.

---

## Risparmio proiettato sui tuoi dati

`python3 scripts/tokens.py estimate` calcola tutto sulle tue sessioni reali,
con le assunzioni stampate in chiaro:

| Taglio dell'overhead | Risparmio totale |
|---|---|
| 30% (conservativo) | **30%** |
| 50% (moderato) | **41%** |
| 70% (aggressivo) | **52%** |

Il guard hook da solo vale ~13%; il resto viene dall'overhead. Per superare il
50% servono entrambi.

### Verificarlo invece di crederci

```bash
python3 scripts/tokens.py report --last 5   # token/turno di oggi
# applica il playbook, riavvia Claude Code, lavora una sessione comparabile
python3 scripts/tokens.py report --last 1   # confronta
```

`baseline` è il numero più pulito per il confronto: è l'overhead fisso, non
dipende da cosa hai fatto nella sessione.

---

## Condividerlo con il team

```bash
cd token-diet
git init && git add -A
git commit -m "token-diet v1.0.0"
git remote add origin <url-del-repo>
git push -u origin main
```

Ogni membro:

```bash
git clone <url-del-repo> token-diet && cd token-diet && ./install.sh
```

Oppure come marketplace di plugin (`.claude-plugin/marketplace.json` è già
pronto): aggiungete il repo come marketplace e installate `token-diet`.

Per la disciplina a livello di progetto, incollate `CLAUDE.md.snippet` nel
`CLAUDE.md` del repo: gli hook la impongono, lo snippet fa collaborare il
modello per default.

---

## Limiti, dichiarati

- Gli hook si caricano **all'avvio della sessione**: dopo l'installazione serve
  un riavvio.
- Il guard blocca e propone la correzione, costando un round-trip da ~50 token.
  Conviene ogni volta che evita più di ~50 token, cioè praticamente sempre.
- La stima dei token da byte (`bytes/4`) è un'approssimazione; le immagini sono
  contate con il modello per area (~1600 token a piena risoluzione, ~400 a
  `scale 0.5`), non per byte.
- I connettori abilitati nell'app desktop si disattivano dalle impostazioni
  dell'app, non da questi file.
- `estimate` è una proiezione con assunzioni esplicite. `report` e `baseline`
  sono misure vere: usa quelle per il verdetto finale.
