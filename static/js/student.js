(function () {
  "use strict";

  var csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  var answeredCount = window.INITIAL_ANSWERED || 0;
  var pendingQueueKey = "econ_pending_" + window.STUDENT_ANSWER_URL;

  function updateProgress() {
    document.getElementById("progress-text").innerText =
      answeredCount + " of " + window.TOTAL_QUESTIONS + " answered";
  }
  updateProgress();

  function getQueue() {
    try {
      return JSON.parse(localStorage.getItem(pendingQueueKey) || "[]");
    } catch (e) {
      return [];
    }
  }
  function setQueue(q) {
    localStorage.setItem(pendingQueueKey, JSON.stringify(q));
  }
  function queuePush(item) {
    var q = getQueue();
    q = q.filter(function (i) { return i.question_id !== item.question_id; });
    q.push(item);
    setQueue(q);
  }
  function queueRemove(questionId) {
    var q = getQueue().filter(function (i) { return i.question_id !== questionId; });
    setQueue(q);
  }

  function postAnswer(questionId, answer, onDone) {
    fetch(window.STUDENT_ANSWER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ question_id: questionId, answer: answer }),
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error(d.error || "Failed"); });
        return res.json();
      })
      .then(function (data) {
        queueRemove(questionId);
        if (typeof data.answered_count === "number") {
          answeredCount = data.answered_count;
          updateProgress();
        }
        if (onDone) onDone(true);
      })
      .catch(function (err) {
        queuePush({ question_id: questionId, answer: answer });
        if (onDone) onDone(false, err.message);
      });
  }

  function flushQueue() {
    var q = getQueue();
    q.forEach(function (item) {
      postAnswer(item.question_id, item.answer);
    });
  }
  window.addEventListener("online", flushQueue);
  setInterval(flushQueue, 8000);
  flushQueue();

  window.selectAnswer = function (el) {
    var qid = parseInt(el.getAttribute("data-qid"), 10);
    var letter = el.getAttribute("data-letter");
    var card = el.closest(".question-card, .compact-answers");
    var group = el.parentElement;

    var wasAnswered = group.querySelector(".option-btn.selected") !== null;

    if (wasAnswered && !window.ALLOW_ANSWER_CHANGE && !el.classList.contains("selected")) {
      return; // answer already locked in for this question
    }

    group.querySelectorAll(".option-btn").forEach(function (b) { b.classList.remove("selected"); });
    el.classList.add("selected");

    var navBtn = document.getElementById("nav-" + el.closest("[data-qnum]").getAttribute("data-qnum"));

    postAnswer(qid, letter, function (ok, errMsg) {
      if (ok) {
        if (navBtn) navBtn.classList.add("answered");
      } else if (errMsg) {
        console.warn("Answer save queued for retry:", errMsg);
      }
    });

    if (!wasAnswered) {
      answeredCount += 1;
      updateProgress();
    }
  };

  window.scrollToQuestion = function (qnum) {
    var card = document.getElementById("q-card-" + qnum);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  // Countdown timer. If STRICT_TIMER is on, expiry locks the answer sheet
  // and sends the student to Review (server also rejects late answer saves
  // independently — this client lock is a UX convenience, not the security
  // boundary).
  if (window.DURATION_MINUTES && window.DURATION_MINUTES > 0) {
    var started = new Date(window.STARTED_AT_ISO).getTime();
    var deadline = started + window.DURATION_MINUTES * 60000;
    var timerEl = document.getElementById("timer");
    var lockedTriggered = false;

    function lockAnswerSheet() {
      if (lockedTriggered) return;
      lockedTriggered = true;
      document.querySelectorAll(".option-btn").forEach(function (b) {
        b.style.pointerEvents = "none";
        b.style.opacity = "0.6";
      });
      var banner = document.createElement("div");
      banner.className = "banner banner-warning";
      banner.style.position = "sticky";
      banner.style.top = "60px";
      banner.style.zIndex = "45";
      banner.innerText = "Time's up — your answers are locked. Redirecting to submit...";
      document.querySelector(".questions-col").prepend(banner);
      setTimeout(function () {
        window.location.href = window.REVIEW_URL;
      }, 2000);
    }

    function tick() {
      var remaining = Math.max(0, deadline - Date.now());
      var mins = Math.floor(remaining / 60000);
      var secs = Math.floor((remaining % 60000) / 1000);
      timerEl.innerText = (mins < 10 ? "0" : "") + mins + ":" + (secs < 10 ? "0" : "") + secs;
      if (remaining <= 0) {
        timerEl.innerText = "Time's up";
        timerEl.style.color = "#c62828";
        clearInterval(timerInterval);
        if (window.STRICT_TIMER) lockAnswerSheet();
      }
    }
    tick();
    var timerInterval = setInterval(tick, 1000);
  }
})();
