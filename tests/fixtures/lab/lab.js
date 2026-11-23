// Lab pages report what really happened (cells typed, form sent, card moved) to the eval server, so the
// evaluation checks results in the page, not only the words of the agent's answer.
window.labRecord = function (name, data) {
  try {
    fetch('/__state/' + name, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  } catch (e) {}
};
