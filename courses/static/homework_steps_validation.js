document.addEventListener(
  "submit",
  (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-homework-autosave]")) return;

    const requiredForFinalSubmit = form.querySelectorAll("[data-final-submit-required]");
    if (!requiredForFinalSubmit.length) return;

    const isDraftSave = event.submitter?.name === "intent" && event.submitter.value === "save";
    form.querySelectorAll('input[type="hidden"][name="intent"]').forEach((input) => input.remove());
    requiredForFinalSubmit.forEach((field) => {
      field.required = !isDraftSave;
    });
  },
  true,
);
