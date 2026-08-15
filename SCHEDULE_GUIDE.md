# YouTube Music Scrobbler: Scheduling & History Guide ⏱️

This document explains the optimal GitHub Actions schedule configuration, how YouTube Music history deduplication works, replay detection mechanics, and compliance with GitHub Terms of Service (ToS).

---

## 📅 Recommended 30-Minute Schedule Configuration

The workflow schedule in `.github/workflows/sync.yml` is configured to run every **30 minutes** using an **odd minute offset**:

```yaml
on:
  schedule:
    - cron: '17,47 * * * *' # Runs at :17 and :47 past every hour
```

### Why Odd Minute Offsets (`17,47` instead of `*/30`)?
* **Avoids Runner Queue Congestion:** Thousands of GitHub Action workflows trigger at exact top-of-hour (`:00`) and half-hour (`:30`) marks (`*/30 * * * *`). This creates server load spikes that cause execution delays ranging from 15 to 45 minutes.
* **Instant Execution:** Running at `:17` and `:47` bypasses peak server load, ensuring your scrobbler runs on time reliably without queue delays.

---

## 🎵 YouTube Music History Behavior & Replay Logic

### 1. How YouTube Music Handles Duplicate Plays
YouTube Music's internal API deduplicates recent plays in its history array:
* **Interleaved Plays (`A → B → A`):** When you play `Song A`, then `Song B`, then `Song A` again in a short period, YouTube Music moves `Song A` to the top (position 1) and collapses its previous record. The history API returns `[Song A, Song B]`.
* **Continuous Loops (`A → A → A`):** Playing a single song on repeat maintains only **1 instance** of `Song A` at position 1 in the YTM history API.

### 2. How the Scrobbler Processes Plays Across Schedule Runs

| Listening Pattern | YTM API Output | Scrobbler Evaluation & DB Action | Scrobbles Count |
| :--- | :--- | :--- | :--- |
| **`A → B → A` (within 1 run)** | `[Song A (pos 1), Song B (pos 2)]` | Both songs are evaluated as `new_song`. Saved to `data.db`. | **1 for A, 1 for B** |
| **`A → B → A` (across runs)** | Run 1: `[A (pos 1), B (pos 2)]`<br>Run 2: `[B (pos 1), A (pos 2)]`<br>Run 3: `[A (pos 1), B (pos 2)]` | Position Tracker detects `current_position < saved_position` (`reproduction`). | **2 for A, 2 for B** |
| **`A → A → A` (continuous loop)** | Position 1 with `playedAt: "Just now"` | Position Tracker verifies `current_position == 1` & `scrobbled_at > 120s ago` (`loop_reproduction`). | **1 scrobble per 30-min run** |

---

## ⚖️ GitHub Actions Terms of Service & Policy Compliance

Running this workflow every 30 minutes is **100% compliant with GitHub Terms of Service (ToS)**:

1. **Cron Frequency Limits:** GitHub Actions allows cron frequencies up to **every 5 minutes** (`*/5 * * * *`). A 30-minute interval (`17,47 * * * *`) is well within platform policy.
2. **Free Minute Quota:** 
   * **Public Repositories:** 100% free with unlimited minutes.
   * **Private Repositories:** Each scrobble run takes ~30 seconds (~60 runs/day = ~30 minutes/month). This uses **less than 2.5%** of the 2,000 free monthly minutes.
3. **60-Day Inactivity Rule:** If a repository receives no commits or activity for 60 days, GitHub automatically pauses scheduled workflows. Push any commit or manually trigger the workflow to keep it active.
4. **Acceptable Use:** Automated scrobbling, backup, and API stats synchronization are permitted CI/CD utility workflows under GitHub's Acceptable Use Policy.
