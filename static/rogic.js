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

const copy_btn = document.getElementById('for_copy');
const input_form = document.querySelector('textarea[name="input_form"]');
const toast = document.getElementById('toast');

function showToast(){
    toast.classList.remove('hidden');
    toast.classList.add('show');

    setTimeout(()=>{
        toast.classList.remove('show');
        toast.classList.add('hidden');
    }, 2000);
}

copy_btn.addEventListener('click', ()=>{
    const textToCopy = input_form.value;

    navigation.clipboard.writeText(textToCopy)
    .then(()=>{
        showToast();
    });
});