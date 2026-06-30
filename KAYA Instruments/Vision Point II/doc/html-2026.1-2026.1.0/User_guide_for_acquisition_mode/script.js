var dropdown = document.getElementById('versionControl');

var options = ['2024.1'];

options.forEach(function(optionText) {
  var option = document.createElement('option');
  option.text = optionText;
  dropdown.add(option);
});

function changeUrl() {
  var selectedVersion = dropdown.value;
  var currentUrl = window.location.href;
  var baseUrl = currentUrl.split('.com')[0] + '.com';
  var version = selectedVersion;
  var updatedUrl = baseUrl + '/' + 'VP' + '/' + version;
  var currentUrl_end_ver = currentUrl.replace(baseUrl + '/' + 'VP' + '/', '');
  var currentUrl_end = currentUrl_end_ver.replace(currentUrl_end_ver.split('/')[0], '')
  updatedUrl += currentUrl_end;
  console.log('Updated URL:', updatedUrl);
  window.location.href = updatedUrl;
}

dropdown.addEventListener('change', changeUrl);

document.addEventListener('DOMContentLoaded', function() {
    var anchors = document.querySelectorAll('.anchor');
    var currentUrl = window.location.pathname;

    anchors.forEach(function(anchor) {
        var id = anchor.id;
        var page = currentUrl;
        anchor.setAttribute('href', page + '#' + id);
    });
});

document.addEventListener("DOMContentLoaded", function() {
        const headings = document.querySelectorAll("h1, h2, h3");

        headings.forEach(function(heading) {
            const anchorLink = document.createElement("a");
            anchorLink.setAttribute("href", "#" + heading.id);
            anchorLink.classList.add("anchor-link");

            heading.appendChild(anchorLink);
        });
    });
document.addEventListener('DOMContentLoaded', function() {
    var downloadButton = document.getElementById('downloadPdfButton');

    downloadButton.addEventListener('click', function() {
        var downloadFileName = 'Vision_Point_II_App_User_Guide_For_Acquisition_Mode.pdf';
        var link = document.createElement('a');
        link.setAttribute('href', downloadFileName);
        link.setAttribute('download', downloadFileName);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });
});

var projectNameElement = document.getElementById('projectname');
var projectBriefElement = document.getElementById('projectbrief');
var projectNumberElement = document.getElementById('projectnumber');

projectNameElement.childNodes[0].nodeValue = 'User guide for acquisition mode ';
projectBriefElement.childNodes[0].nodeValue = 'User guide for acquisition mode ';
projectNumberElement.childNodes[0].nodeValue = "ver 2026.1-2026.1.0";