const urls = textarea.value.split('\n').filter(u => u.trim());
fetch('/extract', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({urls: urls})
})
.then(res => res.json)
.then(data => {
    document.getElementById('output_section').textContent = data.latex;
})
.catch(err =>{
    console.error('エラー：', err);
    document.getElementById('output_section').textContent = 'Error!! :' + err;
});