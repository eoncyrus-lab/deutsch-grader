(function () {
  "use strict";
  if (window.DGReader) return;

  var synth = window.speechSynthesis;
  var currentButton = null;

  function germanVoice() {
    if (!synth || !synth.getVoices) return null;
    var voices = synth.getVoices() || [];
    for (var i = 0; i < voices.length; i++) {
      if (/^de([-_]|$)/i.test(voices[i].lang || "")) return voices[i];
    }
    return null;
  }

  function normalize(text) {
    return String(text || "")
      .replace(/\[(NAME|CLASS|SCHOOL)\]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function setState(button, active) {
    if (!button) return;
    button.classList.toggle("speaking", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    if (active) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = "⏹ 停止";
    } else if (button.dataset.originalLabel) {
      button.textContent = button.dataset.originalLabel;
    }
  }

  function speak(text, button) {
    var value = normalize(text);
    if (!value) return false;
    if (!synth || typeof SpeechSynthesisUtterance === "undefined") {
      document.dispatchEvent(new CustomEvent("dg-tts-warning", {
        detail: "当前浏览器不支持语音朗读，请换用 Chrome、Edge 或 Safari。"
      }));
      return false;
    }
    synth.cancel();
    var utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = "de-DE";
    utterance.rate = 0.88;
    var voice = germanVoice();
    if (voice) utterance.voice = voice;
    if (!voice) {
      document.dispatchEvent(new CustomEvent("dg-tts-warning", {
        detail: "系统没有找到德语语音包，会尝试用默认语音朗读。"
      }));
    }
    if (currentButton) setState(currentButton, false);
    currentButton = button || null;
    setState(currentButton, true);
    utterance.onend = function () {
      setState(currentButton, false);
      currentButton = null;
    };
    utterance.onerror = function () {
      setState(currentButton, false);
      currentButton = null;
    };
    synth.speak(utterance);
    return true;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-speak]");
    if (!button) return;
    event.preventDefault();
    var text = button.getAttribute("data-speak");
    if (currentButton === button && synth && synth.speaking) {
      synth.cancel();
      setState(button, false);
      currentButton = null;
      return;
    }
    speak(text, button);
  });

  window.DGReader = {
    speak: function (text) { return speak(text, null); },
    germanVoice: germanVoice,
    stop: function () { if (synth) synth.cancel(); setState(currentButton, false); currentButton = null; }
  };
})();
