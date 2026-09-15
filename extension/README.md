# One-click application autofill (Chrome extension)

Fills any job application form **in your own browser**, from the same profile the cloud agent uses.

Because it runs where you're already signed in, it works on the sites the cloud agent *can't* do —
**Workday, iCIMS, Taleo, LinkedIn Easy Apply** — and there's no CAPTCHA problem, because you're a
real person in a real browser.

## Install (2 minutes, once)

```bash
python scripts/build_extension.py     # generates extension/profile.json from your brain
```

1. Chrome → `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. **Load unpacked** → select this `extension/` folder
4. Pin the extension to your toolbar

## Use

1. Open any job application page
2. Click the extension → **⚡ Fill the form**
3. It lists every field it filled and what it answered, plus anything it left for you
4. **Review, then submit yourself**

**📋 Copy answer sheet** puts all your standard answers + your banked question answers on the
clipboard — useful for multi-step wizards or anything the filler can't reach.

## Keeping it in sync

After editing `data/applicant_profile.yaml`:

```bash
python scripts/build_extension.py
```

Then hit **Reload** on the extension card in `chrome://extensions`.

## What it fills

Name, email, phone, address + postcode, location, LinkedIn/GitHub/portfolio, current company,
right-to-work / visa status, sponsorship questions, notice period, start date, salary (single, min
and max), how-you-heard, years of experience, and the diversity questions (gender, ethnicity, age,
sexual orientation, disability, neurodiversity, veteran, socio-economic background), plus consent
checkboxes.

Open questions ("why this company?") are deliberately **left for you** — they should be tailored per
role. Use the answer sheet, or the tailored answers the cloud agent writes for supported ATS sites.

## Notes

- It never submits. You always review and press submit.
- It skips fields that already have a value, so it won't overwrite your edits.
- It only reads the page you're on, and your profile never leaves your machine.
