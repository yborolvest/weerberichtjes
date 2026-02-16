# Fix "400" when pushing to a new repo

## 1. Stop tracking large/generated files (so they aren’t pushed)

Run in the project folder:

```bash
git rm --cached ramadan_vandaag.mp4 2>/dev/null || true
git rm --cached ramadan_voice_timing.json 2>/dev/null || true
git status
```

Commit the change:

```bash
git add .gitignore
git commit -m "Ignore generated Ramadan files and all mp4; remove from tracking"
```

## 2. Make sure the new repo exists

- On GitHub: **New repository** → name e.g. `ramadanbot` → **Create repository** (no README/license is fine).
- Copy the repo URL (HTTPS or SSH), e.g. `https://github.com/USERNAME/ramadanbot.git`.

## 3. Push to the new remote

If you haven’t added the remote yet:

```bash
git remote add ramadanbot https://github.com/USERNAME/ramadanbot.git
```

Push (first time):

```bash
git push -u ramadanbot main
```

If your default branch is `master`:

```bash
git push -u ramadanbot master
```

## 4. If you still get 400

- **Repo name/URL**  
  Check the URL in a browser. Ensure it’s exactly `https://github.com/yborolvest/ramadanbot.git` (or your username/repo).

- **Authentication**  
  - HTTPS: use a [Personal Access Token](https://github.com/settings/tokens) instead of your password when Git asks.  
  - Or use SSH:  
    `git remote set-url ramadanbot git@github.com:yborolvest/ramadanbot.git`  
    then `git push -u ramadanbot main`.

- **Create repo with a README**  
  On GitHub, create the repo, add a README, then:

  ```bash
  git pull ramadanbot main --allow-unrelated-histories
  git push -u ramadanbot main
  ```

- **Verbose push**  
  To see the real error:  
  `GIT_CURL_VERBOSE=1 git push ramadanbot main`
