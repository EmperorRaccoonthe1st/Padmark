# Feature & Keybinding Plan: In-Session Markup Controls

**Document Date:** October 1, 2026  
**Target:** `padmark.py` in-session keyboard control architecture

---

## 1. Executive Concept

While `padmark` is actively capturing trackpad input, it holds exclusive keyboard focus (`GtkLayerShell.KeyboardMode.EXCLUSIVE`). This allows every keystroke to trigger instant, zero-latency tool changes with **zero GUI widgets, menus, or screen clutter**.

Every stroke is stored as an independent vector object containing its own:
- Points array `[(x, y), ...]`
- Color `(r, g, b, a)`
- Line width
- Blend operator (`OPERATOR_OVER` for pen, `OPERATOR_MULTIPLY` or reduced alpha for highlighter)

---

## 2. Proposed Feature Inventory

### Feature 1: Dynamic Color & Palette Switching
- **Behavior:** Change the active ink color without affecting previously drawn strokes.
- **Visual Feedback:** The dashed bounding box border immediately shifts its glow color to match the currently selected pen color.
- **Keybindings:**
  - Numbers `1` – `5` (or mnemonic keys):
    - `1`: Cyan / Electric Blue (default high contrast)
    - `2`: Yellow / Gold
    - `3`: Coral Red (annotations, bug pointers, warnings)
    - `4`: Emerald Green (confirmations, checkmarks)
    - `5`: Hot Pink / Magenta
    - `6`: Crisp White
  - Or letter mnemonics: `r` (red), `g` (green), `b` (blue), `y` (yellow), `w` (white).

### Feature 2: Pen Thickness & Highlighting
- **Pen Thickness:**
  - Cycle between 3 distinct presets: **Fine (2px)** $\rightarrow$ **Medium (4px)** $\rightarrow$ **Bold (8px)**.
  - Keybinding: `t` (thickness) or `+` / `-` / `[` / `]`.
- **Highlighter Mode:**
  - Toggles between opaque pen ($a = 0.95$, width $3.5\text{px}$) and wide translucent highlighter ($a = 0.35$, width $18\text{px}$).
  - Highlighter strokes don't completely cover text underneath; background content remains readable.
  - Keybinding: `h` or `a` (alpha/highlighter).

### Feature 3: In-Session Mode Toggling (Temporary $\leftrightarrow$ Permanent)
- **Behavior:** Change whether hitting `Esc` will dismiss the markup (`temp`) or pin it to the screen (`perm`).
- **Visual Feedback:**
  - Dashed outline = Temporary mode (will vanish on Esc).
  - Solid glow outline = Permanent mode (will pin on Esc).
- **Keybinding:** `p` (perm toggle) or `m` (mode).

### Feature 4: Stroke History Management (Undo & Clear)
- **Undo (`u` or `Ctrl+z`):**
  - Pops the most recent stroke from the stack, allowing quick correction of mistakes.
- **Redo (`Shift+u` or `r`):**
  - Re-applies the most recently undone stroke.
- **Clear Canvas (`c` or `Backspace`):**
  - Wipes all strokes inside the current active box without exiting or closing the tool.

### Feature 5: Arrow & Geometric Snapping (Optional Advanced)
- **Straight Line / Arrow Mode (`s` or hold `Shift`):**
  - Snaps the current stroke into a straight line between touchdown and liftoff.
  - Auto-draws an arrow head at the liftoff point.

---

## 3. Implemented Keyboard Mapping

| Key | Action | Description |
| :--- | :--- | :--- |
| **`s`** | **Thin** | Decreases pen/highlighter thickness ($20\text{px} \to 12\text{px} \to 7\text{px} \to 4\text{px} \to 2.5\text{px} \to 1.5\text{px}$). |
| **`d`** | **Thick** | Increases pen/highlighter thickness ($1.5\text{px} \to 2.5\text{px} \to 4\text{px} \to 7\text{px} \to 12\text{px} \to 20\text{px}$). |
| **`a`** | **Highlighter Mode** | Toggles wide translucent highlighter ($\alpha=0.35$) vs. opaque pen ($\alpha=0.95$). |
| **`u`** | **Undo** | Removes the last drawn stroke. |
| **`Shift + u`** | **Redo** | Restores the last undone stroke. |
| **`1` – `6`** | **Color Presets** | `1`=Cyan, `2`=Yellow, `3`=Red, `4`=Green, `5`=Magenta, `6`=White. |
| **`p`** | **Perm/Temp Toggle** | Toggles whether pressing `Esc` keeps markup permanent or dismisses it. |
| **`c`** | **Clear** | Clears all strokes inside the current box without closing. |
| **`Esc`** | **Finish** | Ungrabs trackpad; finishes markup according to current mode. |

---

## 4. Minimalist Visual Feedback (Zero-Clutter OSD)

To preserve the zero-clutter philosophy, no buttons or permanent toolbars are drawn:
1. **Adaptive Border Color:** The bounding box outline dynamically matches the active pen color.
2. **Border Style for Mode:**
   - **Dashed Border:** Temporary Mode (will vanish on Esc).
   - **Solid Border:** Permanent Mode (will pin to desktop on Esc).
3. **Corner Badge (Fading Toast):**
   - When a key is pressed (e.g. changing color or thickness), a subtle 12px pill label appears in the top-left of the box (e.g. `[RED 4px]`, `[HIGHLIGHTER]`, `[PERM]`) and fades away after 1 second.
