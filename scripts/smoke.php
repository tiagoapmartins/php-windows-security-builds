<?php
function check_build($condition, $message) {
    if (!$condition) { throw new RuntimeException($message); }
}
check_build(PHP_INT_SIZE === 8, 'Expected an x64 build');
foreach (array('curl','mbstring','intl','gd','zip','openssl','sodium','mysqli','pdo_mysql','pdo_sqlite','dom','session','zlib') as $extension) {
    check_build(extension_loaded($extension), 'Missing extension: ' . $extension);
}
check_build(json_decode(json_encode(array('ok' => true)), true)['ok'] === true, 'JSON round trip failed');
check_build(gzdecode(gzencode('compression check')) === 'compression check', 'Compression failed');
check_build(mb_strlen("\xE6\x97\xA5\xE6\x9C\xAC", 'UTF-8') === 2, 'Multibyte text check failed');
check_build(Normalizer::normalize("e\u{0301}") === "\u{00E9}", 'ICU normalization failed');
check_build(openssl_digest('php', 'sha256') === hash('sha256', 'php'), 'OpenSSL digest failed');
check_build(strlen(random_bytes(16)) === 16, 'Random bytes failed');
$database = new PDO('sqlite::memory:');
check_build((int)$database->query('SELECT 42')->fetchColumn() === 42, 'SQLite query failed');
$image = imagecreatetruecolor(2, 2);
check_build($image !== false, 'GD image creation failed');
imagedestroy($image);
check_build(simplexml_load_string('<check>ok</check>')->__toString() === 'ok', 'XML parsing failed');
$modules = get_loaded_extensions();
sort($modules);
echo json_encode(array('version'=>PHP_VERSION,'versionId'=>PHP_VERSION_ID,'x64'=>PHP_INT_SIZE===8,'zts'=>(bool)PHP_ZTS,'modules'=>$modules,'curl'=>curl_version()['version'],'openssl'=>OPENSSL_VERSION_TEXT,'icu'=>INTL_ICU_VERSION,'status'=>'passed'), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), "\n";
