/* Fill engine — a JS port of the Python deterministic matcher that fills Lever/Greenhouse forms.
 * Runs in YOUR signed-in browser, so it also works on Workday / iCIMS / LinkedIn where the cloud
 * agent can't reach a form at all.
 *
 * Returns {filled, skipped, details[]} so the popup can show exactly what it did.
 */
(function () {
  const MONTHS = ["January","February","March","April","May","June","July","August","September",
                  "October","November","December"];
  const PLACEHOLDER = ["select","please select","gender","ethnicity","age bracket","--","choose",
                       "select...","select an option",""];

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const low  = (s) => norm(s).toLowerCase();

  // ---- option matching: EXACT first, then substring (so "Man" never ticks "Woman") -------------
  function pickOption(desired, options) {
    if (!desired || !options || !options.length) return null;
    const d = low(desired);
    for (const o of options) if (low(o) === d) return o;
    for (const o of options) {
      const ol = low(o);
      if (!ol || PLACEHOLDER.includes(ol)) continue;
      if (ol.includes(d) || d.includes(ol)) return o;
    }
    return null;
  }
  const firstPresent = (prefs, options) => {
    for (const p of prefs) { const o = pickOption(p, options); if (o) return o; }
    return null;
  };
  const optHas = (opts, ...subs) =>
    subs.some((s) => opts.some((o) => low(o).includes(s)));

  function nextMonth() { return MONTHS[new Date().getMonth() === 11 ? 0 : new Date().getMonth() + 1]; }

  // ---- find the QUESTION for a field (not the option's own label) ------------------------------
  function labelFor(el) {
    let t = el.getAttribute("aria-label") || "";
    if (!t && el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) t = l.innerText;
    }
    if (!t) {
      let p = el.closest("li,.application-question,.form-group,fieldset,section,div"), hops = 0;
      while (p && !t && hops < 5) {
        const lab = p.querySelector("label,.application-label,legend,h2,h3,h4,p");
        if (lab && norm(lab.innerText)) t = lab.innerText;
        p = p.parentElement; hops++;
      }
    }
    if (!t) t = el.getAttribute("placeholder") || el.getAttribute("name") || "";
    return norm(t).slice(0, 250);
  }

  function groupLabelFor(el) {
    const own = el.closest("label");
    let p = el.closest(".application-question, fieldset, li, .form-group, section, div"), hops = 0;
    while (p && hops < 7) {
      const cand = p.querySelector(".application-label, legend, h2, h3, h4");
      if (cand && cand !== own && norm(cand.innerText)) return norm(cand.innerText).slice(0, 250);
      const labs = [...p.querySelectorAll("label")].filter(
        (l) => l !== own && norm(l.innerText) && !l.querySelector("input[type=radio],input[type=checkbox]"));
      if (labs.length) return norm(labs[0].innerText).slice(0, 250);
      p = p.parentElement; hops++;
    }
    return norm(el.getAttribute("aria-label") || el.name || "").slice(0, 250);
  }

  // ---- setting values so React/Angular actually notice ------------------------------------------
  function setNative(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                    : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, value); else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function clickChoice(el) {
    let lab = el.closest("label");
    if (!lab && el.id) lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (!lab) lab = el.parentElement?.querySelector("label") || el.parentElement;
    try { (lab || el).click(); } catch (e) {}
    try {
      el.checked = true;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (e) {}
    return !!el.checked;
  }

  // ---- decide the answer for one field ---------------------------------------------------------
  function decide(lab, kind, opts, k) {
    const l = low(lab);
    const optsL = opts.map(low);
    const genderExcl = ["same as", "assigned at birth", "transgender", "identity the same"]
      .some((x) => l.includes(x));
    const isGender  = (l.includes("gender") && !genderExcl) ||
                      (optHas(opts, "woman", "female") && optHas(opts, "man", "male"));
    const isEthnic  = l.includes("ethnic") || l.includes("race") ||
                      (optHas(opts, "asian") && optHas(opts, "white", "black", "mixed"));
    const isAge     = /\bage\b/.test(l) || opts.some((o) => /\d\d\s*[-–]\s*\d\d/.test(o));
    const isOrient  = l.includes("orientation") || optHas(opts, "heterosexual", "bisexual", "gay");

    if (l.includes("if other") || l.includes("please specify") || l.includes("if yes")) return null;
    if (l.includes("full name") || (/\bname\b/.test(l) &&
        !["first","last","sur","given","middle","preferred","user","file","company"].some((x) => l.includes(x))))
      return k.name;
    if (l.includes("first name") || l.includes("given name")) return (k.name || "").split(" ")[0];
    if (l.includes("last name") || l.includes("surname") || l.includes("family name"))
      return (k.name || "").split(" ").slice(-1)[0];
    if (l.includes("email")) return k.email;
    if (l.includes("phone") || l.includes("mobile") || l.includes("telephone")) return k.phone;
    if (l.includes("preferred name") || l.includes("known as")) return (k.name || "").split(" ")[0];
    if (l.includes("pronoun")) return firstPresent([k.pronouns, "He/Him"], opts) || k.pronouns;
    if (l.includes("current company") || l.includes("employer")) return k.current_company;
    if (l.includes("linkedin")) return k.linkedin;
    if (l.includes("github")) return k.github;
    if (l.includes("portfolio") || l.includes("personal site")) return k.portfolio;
    if (l.includes("postcode") || l.includes("postal code") || /\bzip\b/.test(l)) return k.postcode;
    if (l.includes("address line 2") || l.includes("apartment") || l.includes("suite")) return k.addr_line2;
    if (l.includes("address line 1") || l.includes("street address")) return k.addr_line1;
    if (/\baddress\b/.test(l) && !l.includes("email")) return k.addr_full || k.addr_line1;
    if (/\b(county|region|province)\b/.test(l)) return k.county;
    if (l.includes("nationality") || l.includes("citizenship")) return k.nationality;
    if (l.includes("country of birth") || l.includes("country of origin") || l.includes("where are you from"))
      return k.home_country;
    if (l.includes("country")) return firstPresent([k.current_country, "United Kingdom"], opts) || k.current_country;
    if (l.includes("location") || l.includes("based") || l.includes("where do you live")) return k.city_location;
    if (/\b(city|town)\b/.test(l)) return k.city;
    if (l.includes("date of birth") || /\bdob\b/.test(l) ||
        (l.includes("birth") && (l.includes("date") || l.includes("born")))) return k.dob;
    if (isOrient) return firstPresent([k.orientation, "Heterosexual", "Straight"], opts) || k.orientation;
    if (l.includes("background check") || /\bdbs\b/.test(l) || l.includes("vetting"))
      return firstPresent(["Yes", "I consent"], opts) || "Yes";
    if (l.includes("criminal") || l.includes("convict") || l.includes("unspent") || l.includes("caution"))
      return firstPresent(["No", "None"], opts) || "No";
    if (l.includes("right to work") || (l.includes("authori") && l.includes("work")) ||
        l.includes("eligible to work") || l.includes("visa") || l.includes("sponsor")) {
      const isStatus = optHas(opts, "visa", "citizen", "settled", "graduate", "indefinite", "national",
                              "right to work") || opts.some((o) => o.length > 40);
      if (isStatus)
        return firstPresent(["I have a Graduate Visa", "have a Graduate Visa", "Graduate Visa",
                             "temporary right to work in the UK, and might need sponsorship",
                             "temporary right to work", "full unrestricted right to work", "Yes"], opts)
               || k.rtw_uk;
      if (l.includes("sponsor") && (l.includes("require") || l.includes("need")))
        return firstPresent([l.includes("future") ? "Yes" : "No"], opts) || (l.includes("future") ? "Yes" : "No");
      return firstPresent(["Yes"], opts) || "Yes";
    }
    if (l.includes("notice period") || l.includes("notice")) return pickOption(k.notice, opts) || k.notice;
    if (l.includes("start date") || l.includes("availab") || l.includes("when can you start")) {
      if (opts.some((o) => MONTHS.includes(norm(o)))) return pickOption(nextMonth(), opts);
      return firstPresent([k.notice, "1 month", "Available now"], opts) || "Within 1 month of an offer";
    }
    if ((l.includes("salary") || l.includes("compensation")) && (l.includes("minimum") || /\bmin\b/.test(l)))
      return "£" + Number(k.salary_min || 40000).toLocaleString();
    if ((l.includes("salary") || l.includes("compensation")) && (l.includes("maximum") || /\bmax\b/.test(l)))
      return "£" + Number(k.salary_max || 55000).toLocaleString();
    if (l.includes("salary") || l.includes("compensation") || l.includes("remuneration")) return k.salary;
    if (l.includes("hear about") || l.includes("did you hear") || l.includes("how did you find"))
      return firstPresent([k.heard, "LinkedIn", "Job board", "Website", "Other"], opts) || k.heard;
    if (isGender) return firstPresent([k.gender, "Male", "Man"], opts) || k.gender;
    if (isEthnic) return firstPresent([k.ethnicity, "Indian", "Asian or Asian British - Indian",
                                       "South Asian", "Asian"], opts) || k.ethnicity;
    if (isAge) return pickOption(k.age, opts) || k.age;
    if (l.includes("disab")) return firstPresent([k.disability, "No", "Prefer not to say"], opts) || "No";
    if (l.includes("neurodiver")) return firstPresent(["No", "Prefer not to say"], opts) || "No";
    if (l.includes("gender identity the same") || l.includes("same as the sex"))
      return firstPresent(["Yes"], opts) || "Yes";
    if (l.includes("transgender")) return firstPresent(["No", "Prefer not to say"], opts) || "No";
    if (l.includes("first generation") || (l.includes("first in") && l.includes("universit")))
      return firstPresent(["No"], opts) || "No";
    if (l.includes("veteran") || l.includes("armed forces"))
      return firstPresent(["No", "I am not a veteran", "Prefer not to say"], opts) || "No";
    if (l.includes("relocat") || l.includes("willing to move")) return firstPresent([k.relocate, "Yes"], opts) || "Yes";
    if (l.includes("years of experience") || l.includes("how many years")) return k.years;
    if (kind === "radio" && (optHas(opts, "python", "sql", "java") || l.includes("language")))
      return firstPresent([k.coding, "Python"], opts);
    if (kind === "checkbox" &&
        ["consent","agree","gdpr","privacy","retain","terms","declare","confirm"].some((x) => l.includes(x)))
      return "yes";
    return null;
  }

  // ---- walk the page and fill -------------------------------------------------------------------
  function run(profile) {
    const k = (profile && profile.answers) || {};
    const details = [];
    let filled = 0, skipped = 0;
    const radioGroups = {};

    for (const el of document.querySelectorAll("input, select, textarea")) {
      const type = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button", "file", "search", "image", "reset"].includes(type)) continue;
      if (el.disabled || el.readOnly) continue;

      if (type === "radio") {
        const name = el.name || "r" + Math.random();
        (radioGroups[name] = radioGroups[name] || []).push(el);
        continue;
      }
      if (type === "checkbox") {
        const lab = labelFor(el);
        const v = decide(lab, "checkbox", [], k);
        if (v && String(v).toLowerCase() === "yes" && !el.checked) {
          clickChoice(el); filled++; details.push({ q: lab, a: "✔ ticked" });
        }
        continue;
      }
      if (el.tagName.toLowerCase() === "select") {
        const opts = [...el.options].map((o) => norm(o.text)).filter(Boolean);
        const lab = labelFor(el);
        const v = decide(lab, "select", opts, k);
        const m = v ? pickOption(String(v), opts) : null;
        if (m) {
          const opt = [...el.options].find((o) => norm(o.text) === m);
          if (opt) {
            el.value = opt.value;
            el.dispatchEvent(new Event("change", { bubbles: true }));
            filled++; details.push({ q: lab, a: m });
          }
        } else { skipped++; details.push({ q: lab, a: null }); }
        continue;
      }
      // text / textarea / email / tel / url / date
      const lab = labelFor(el);
      const v = decide(lab, "text", [], k);
      if (v) {
        if (!norm(el.value)) { setNative(el, String(v)); filled++; details.push({ q: lab, a: String(v) }); }
      } else { skipped++; details.push({ q: lab, a: null }); }
    }

    // radio groups: decide once per group using the QUESTION, not the first option's text
    for (const name in radioGroups) {
      const els = radioGroups[name];
      const lab = groupLabelFor(els[0]);
      const opts = els.map((e) => {
        const l2 = e.closest("label");
        return norm(l2 ? l2.innerText : (e.value || ""));
      });
      const v = decide(lab, "radio", opts, k);
      const m = v ? pickOption(String(v), opts) : null;
      if (m) {
        const idx = opts.findIndex((o) => low(o) === low(m));
        if (idx >= 0 && !els[idx].checked) { clickChoice(els[idx]); filled++; details.push({ q: lab, a: m }); }
      } else { skipped++; details.push({ q: lab, a: null }); }
    }
    return { filled, skipped, details };
  }

  // entry point used by the popup
  window.__jobAutofillRun = run;
  return true;
})();
