# NexusMCP: panel de Nexus dentro de E3D

Con el panel **Nexus MCP** se arranca y se para Nexus, la pasarela HTTP que usa Claude (servidor MCP de
`mcp/`), desde la propia sesión gráfica de E3D Design, y se cambia su modo entre **Lectura** y **Edición**.
No hace falta abrir E3D con un `.bat` especial: se puede abrir E3D de forma normal (solo lectura) y pasar a
edición desde el panel. Nexus usa el usuario y el MDB de la sesión, así que no pide contraseña.

```
NexusMCP/
  forms/       nexusmcp.pmlfrm                               (el panel)
  functions/   nexuspanel, nexusstart, nexusstop, nexusisrunning,
               nexusport, nexusurl, nexusroot, nexusmsg      (PML)
  dll/         AVEVA_Nexux_Addin.dll, Newtonsoft.Json.dll    (las añade scripts\package-pmllib.ps1)
  uic/         NexusMCP.uic                                  (pestaña "Nexus MCP" con el botón "Nexus")
```

## Abrir el panel
Cualquiera de estas tres formas:

- **Ventana de comandos de E3D:** `!!nexuspanel()`
- **Botón "Nexus"** de la pestaña *Nexus MCP* de la cinta, si el `.uic` está instalado (ver *Instalación*).
- **Al arrancar E3D** con el lanzador del runtime: `Start-E3D-GUI-Writable.ps1 -Panel -ReadOnly`
  (o el `.bat` con los mismos parámetros). En ese caso no hace falta instalar nada en la PMLLIB del proyecto.
  El lanzador copia este PML a `run\<proyecto>-gui\pmllib` y lo pone el primero en la PMLLIB, a través de una
  copia del fichero `init` del proyecto con una línea más al final. Añadirlo a la PMLLIB en la línea de
  comandos no sirve, porque el `evars.init` de E3D la vuelve a definir.
  Con `-ReadOnly`, Nexus arranca en solo lectura y la edición se activa desde el panel; sin él, arranca en
  edición, como antes. Con `-NoAutoNexus`, el panel se abre con Nexus parado.

## Qué hace cada control
- **Línea de estado** (con color):
  - gris `Nexus: PARADO`;
  - verde `Nexus: ACTIVO - modo SOLO LECTURA`;
  - naranja `Nexus: ACTIVO - modo EDICIÓN`.

  Debajo aparecen la dirección (`Escuchando en http://localhost:5001/`, o la dirección en la que escuchará al
  iniciar) y la versión de la DLL con su modo de llamadas (`ui-thread` en sesión gráfica).
- **Modo: Lectura / Edición**
  - Con Nexus en marcha, el cambio se aplica **en el momento**.
  - Con Nexus parado, se aplica al pulsar *Iniciar*.
  - Antes de pasar a **Edición** se pide confirmación: Claude podrá modificar el modelo, pero solo en las
    bases de datos autorizadas en el MCP (`write_dbs` de `projects.json`), y solo guardará con su herramienta
    `save_work`. Esa herramienta guarda **también tus cambios pendientes** en esa base.
  - Si dices que no, el panel vuelve a Lectura.
- **Iniciar:** arranca Nexus en esta sesión con el modo elegido. Es `!!nexusstart(TRUE|FALSE)`. Si Nexus ya
  estaba en marcha, por ejemplo arrancado por la macro del lanzador, solo aplica el modo.
- **Parar:** para Nexus. Claude pierde el acceso y la sesión vuelve a solo lectura. Es `!!nexusstop()`. No
  guarda ni libera claims: si Claude ha modificado el modelo, pídele antes que haga `save_work`.
- **Actualizar:** vuelve a leer el estado. El panel también se actualiza solo después de cada acción. El
  mensaje del último resultado, o del último error, sale en la línea de debajo del modo.

**Puerto:** el de `!!nexusPort`; si no está definido, el de la variable de entorno `NEXUS_PORT`; si no, 5001.
Mira `!!nexusport()`. Nexus solo escucha en `localhost`. No lo arranques en el mismo puerto que una sesión TTY
(`start_e3d`).

**Desde la ventana de comandos**, sin panel:
- `!!nexusstart(TRUE)` (edición) o `!!nexusstart(FALSE)` (lectura);
- `!!nexusstop()`;
- `!!nexusisrunning()`, `!!nexusurl()`.

## Instalación en la PMLLIB del proyecto (para el botón de la cinta)
1. Genera la carpeta con `.\scripts\package-pmllib.ps1 [-Runtime <runtime>]`. Copia el PML, el formulario, el
   `.uic` y las DLL en `<runtime>\package\NexusMCP-<versión>`.
2. Copia esa carpeta, con el nombre `NexusMCP`, a una carpeta de la PMLLIB del proyecto, por ejemplo
   `2_Design\`. No hay rutas fijas: `!!nexusroot()` encuentra la carpeta a partir de `nexusstart.pmlfnc`.
3. En E3D, ejecuta `PML REHASH ALL` o reinicia. Desde ese momento funciona `!!nexuspanel()`.
4. Para tener el **botón**, el `.uic` tiene que figurar en un `DesignCustomization.xml` que esté en la
   `CAF_UIC_PATH` del proyecto. Hay que añadir una línea dentro de `<UICustomizationFiles>`:

   ```xml
   <CustomizationFile Name="NexusMCP" Path="\\<servidor>\<proyecto>\ADMIN\Customization\PMLLIB\2_Design\NexusMCP\uic\NexusMCP.uic"/>
   ```

   Ese fichero es compartido por todos los usuarios del proyecto, así que lo tiene que añadir el administrador.
   Después hay que reiniciar E3D. Hasta entonces, `!!nexuspanel()` hace lo mismo.

La pestaña tiene ahora **un solo botón, "Nexus"**, que abre el panel. Los tres botones de antes (Iniciar /
Solo lectura / Parar) no permitían ver el estado y se podía arrancar en escritura sin confirmación.

## Requisitos y límites
- **DLL nueva (1.1.0+ de esta rama).** Con ella el panel lee de la propia DLL el modo real
  (`HostConfig.IsWritable`), la dirección (`Prefix`) y la versión (`Version`).
- **DLL antigua.** El panel funciona, pero:
  - el modo es el último que aplicó `!!nexusstart` o, si no, el `NEXUS_READONLY` con el que arrancó E3D. PML no
    ve los cambios que hace la DLL en el entorno;
  - la versión sale como "DLL de Nexus antigua".

  Cambiar el modo en caliente necesita `SetWritable`. La DLL de una sesión abierta está bloqueada: para
  actualizarla hay que cerrar E3D.
- **Solo en sesión gráfica.** En TTY no hay formularios ("Invalid command when GRAPHICS OFF"), pero las
  funciones `!!nexusstart` / `!!nexusstop` sí funcionan.
- **Codificación.** El formulario se guarda en UTF-8 **con BOM**. Sin BOM, E3D lo lee como ANSI y las tildes
  salen mal. `package-pmllib.ps1` y el lanzador lo conservan.
- **Pruebas automáticas.** `!!nexusmcp.skipconfirm = TRUE` omite la confirmación de Edición. Por defecto es
  FALSE. `!!nexusmcp.setmode(TRUE|FALSE)` simula el cambio de opción. El panel llama a las funciones con
  `!!nexusQuiet = TRUE`, así que no abre avisos modales.
- **Parar desde Claude.** Si Claude, a través de Nexus, ejecuta *Parar*, pierde su propia conexión. La
  respuesta de esa petición se corta.

## Verificado (E3D 3.1 Design, noviembre 2026)
- **Sesión gráfica con la DLL antigua:**
  - el panel se carga y muestra el estado correcto;
  - Lectura y luego Edición mediante los callbacks dejan `ReadOnly:false` en `/api/Session/Info`.
- **Sesión gráfica abierta con `-Panel`, DLL nueva:**
  - el panel aparece al arrancar y el PML se carga desde `run\...\pmllib`;
  - muestra la versión;
  - *Parar* y luego *Iniciar* desde el panel funcionan;
  - al pasar a Lectura, `/api/Session/Info` da `ReadOnly:true` y `PML/Run` responde 403.
- **Sesión TTY:**
  - `!!nexusstop()` y luego `!!nexusstart(TRUE)` funcionan, con el puerto tomado de `!!nexusPort`;
  - `!!nexusstart(FALSE)` deja `ReadOnly:true` y PML bloqueado (403).

Notas de PML aprendidas:
- los nombres de variable no admiten `_`;
- `var !x evar 'NOMBRE_CON_GUION'` necesita las comillas;
- `.unset()` falla con objetos de la DLL;
- se usa el patrón "valor por defecto, intento, `handle any` solo para recuperarse" en vez de `elsehandle none`;
- E3D puede seguir usando una versión anterior de una función ya cargada (`PML REHASH ALL`,
  `PML RELOAD FORM !!nexusmcp`);
- un `para` no pinta el color de fondo: la línea de estado es un `text` no editable.
