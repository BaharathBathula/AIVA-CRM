"""Source of the "AIVA CRM Forms" WordPress plugin, zipped on download by
``wordpress.plugin_zip`` together with an ``aiva-config.php`` holding the
workspace's webhook URL and signing secret.

The plugin hooks the submit actions of the popular form plugins, drops
captcha/password/payment/file fields, and posts the answers to AIVA without
holding up the visitor (``blocking => false``). Requests are signed with
``X-AIVA-Signature: t=<unix>,v1=<HMAC-SHA256("<t>.<body>", secret)>``.
Settings live under Settings → AIVA CRM (``manage_options`` only, Settings API
nonces), and a "Send a test" button checks the connection.
"""

MAIN_PHP = r"""<?php
/**
 * Plugin Name:       AIVA CRM Forms
 * Plugin URI:        https://aiva.example/integrations/wordpress
 * Description:       Sends form submissions from Contact Form 7, WPForms, Gravity Forms, Elementor Pro, Ninja Forms, Fluent Forms and Formidable Forms to AIVA CRM as leads.
 * Version:           {{VERSION}}
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * Author:            AIVA
 * License:           GPL-2.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       aiva-crm-forms
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class AIVA_CRM_Forms {

	const OPTION  = 'aiva_crm_forms';
	const VERSION = '{{VERSION}}';
	const MAX_LEN = 5000;

	/** Field types that are never sent. */
	private static $skip_types = array(
		'password', 'credit-card', 'creditcard', 'stripe-credit-card', 'stripe', 'paypal-commerce', 'payment', 'square',
		'captcha', 'recaptcha', 'hcaptcha', 'turnstile', 'cf-turnstile', 'spam', 'honeypot',
		'html', 'divider', 'pagebreak', 'page', 'section', 'hr', 'submit', 'step',
		'file', 'file-upload', 'fileupload', 'upload', 'signature',
	);

	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'menu' ) );
		add_action( 'admin_init', array( __CLASS__, 'register_settings' ) );
		add_action( 'admin_post_aiva_crm_forms_test', array( __CLASS__, 'send_test' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( __FILE__ ), array( __CLASS__, 'action_links' ) );

		add_action( 'wpcf7_mail_sent', array( __CLASS__, 'contact_form_7' ) );
		add_action( 'wpforms_process_complete', array( __CLASS__, 'wpforms' ), 10, 4 );
		add_action( 'gform_after_submission', array( __CLASS__, 'gravity_forms' ), 10, 2 );
		add_action( 'elementor_pro/forms/new_record', array( __CLASS__, 'elementor' ), 10, 2 );
		add_action( 'ninja_forms_after_submission', array( __CLASS__, 'ninja_forms' ) );
		add_action( 'fluentform/submission_inserted', array( __CLASS__, 'fluent_forms' ), 10, 3 );
		add_action( 'fluentform_submission_inserted', array( __CLASS__, 'fluent_forms' ), 10, 3 );
		add_action( 'frm_after_create_entry', array( __CLASS__, 'formidable' ), 30, 2 );
	}

	/** Form plugins this plugin listens to: key => array( label, installed? ). */
	public static function sources() {
		return array(
			'contact-form-7' => array( 'Contact Form 7', defined( 'WPCF7_VERSION' ) ),
			'wpforms'        => array( 'WPForms', function_exists( 'wpforms' ) ),
			'gravity-forms'  => array( 'Gravity Forms', class_exists( 'GFForms' ) ),
			'elementor'      => array( 'Elementor Pro forms', defined( 'ELEMENTOR_PRO_VERSION' ) ),
			'ninja-forms'    => array( 'Ninja Forms', class_exists( 'Ninja_Forms' ) ),
			'fluent-forms'   => array( 'Fluent Forms', defined( 'FLUENTFORM' ) ),
			'formidable'     => array( 'Formidable Forms', class_exists( 'FrmForm' ) ),
		);
	}

	// ------------------------------------------------------------------ settings

	public static function activate() {
		$current = get_option( self::OPTION );
		if ( is_array( $current ) && ! empty( $current['webhook_url'] ) ) {
			return;
		}
		$bundled = self::bundled();
		$enabled = array();
		foreach ( array_keys( self::sources() ) as $key ) {
			$enabled[ $key ] = 1;
		}
		update_option(
			self::OPTION,
			array(
				'webhook_url' => esc_url_raw( $bundled['webhook_url'], array( 'https', 'http' ) ),
				'secret'      => sanitize_text_field( $bundled['secret'] ),
				'enabled'     => $enabled,
			),
			false
		);
	}

	private static function bundled() {
		$file   = __DIR__ . '/aiva-config.php';
		$config = file_exists( $file ) ? include $file : array();
		return wp_parse_args( is_array( $config ) ? $config : array(), array( 'webhook_url' => '', 'secret' => '', 'workspace' => '' ) );
	}

	private static function options() {
		$saved   = get_option( self::OPTION, array() );
		$saved   = is_array( $saved ) ? $saved : array();
		$enabled = array();
		foreach ( array_keys( self::sources() ) as $key ) {
			$enabled[ $key ] = 1;
		}
		$options = wp_parse_args( $saved, array( 'webhook_url' => '', 'secret' => '', 'enabled' => $enabled ) );
		if ( ! is_array( $options['enabled'] ) ) {
			$options['enabled'] = $enabled;
		}
		return $options;
	}

	public static function register_settings() {
		register_setting(
			'aiva_crm_forms',
			self::OPTION,
			array(
				'type'              => 'array',
				'sanitize_callback' => array( __CLASS__, 'sanitize' ),
				'show_in_rest'      => false,
			)
		);
	}

	public static function sanitize( $input ) {
		$old   = self::options();
		$input = is_array( $input ) ? $input : array();
		$out   = array();

		$url                = isset( $input['webhook_url'] ) ? trim( (string) $input['webhook_url'] ) : '';
		$out['webhook_url'] = esc_url_raw( $url, array( 'https', 'http' ) );
		if ( '' !== $url && '' === $out['webhook_url'] ) {
			add_settings_error( self::OPTION, 'aiva_url', __( 'Enter the webhook URL exactly as AIVA shows it.', 'aiva-crm-forms' ) );
			$out['webhook_url'] = $old['webhook_url'];
		}

		$secret        = isset( $input['secret'] ) ? sanitize_text_field( (string) $input['secret'] ) : '';
		$out['secret'] = '' === $secret ? $old['secret'] : $secret;
		if ( ! empty( $input['clear_secret'] ) ) {
			$out['secret'] = '';
		}

		$out['enabled'] = array();
		foreach ( array_keys( self::sources() ) as $key ) {
			$out['enabled'][ $key ] = empty( $input['enabled'][ $key ] ) ? 0 : 1;
		}
		return $out;
	}

	public static function menu() {
		add_options_page( 'AIVA CRM', 'AIVA CRM', 'manage_options', 'aiva-crm-forms', array( __CLASS__, 'page' ) );
	}

	public static function action_links( $links ) {
		$url = admin_url( 'options-general.php?page=aiva-crm-forms' );
		array_unshift( $links, '<a href="' . esc_url( $url ) . '">' . esc_html__( 'Settings', 'aiva-crm-forms' ) . '</a>' );
		return $links;
	}

	public static function page() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$options = self::options();
		// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- display-only flag set by our own redirect.
		$test = isset( $_GET['aiva_test'] ) ? sanitize_key( wp_unslash( $_GET['aiva_test'] ) ) : '';
		?>
		<div class="wrap">
			<h1><?php esc_html_e( 'AIVA CRM', 'aiva-crm-forms' ); ?></h1>
			<p><?php esc_html_e( 'Form submissions on this site are sent to AIVA CRM, where they become leads. Visitors are never slowed down: if AIVA can\'t be reached, the form still works as usual.', 'aiva-crm-forms' ); ?></p>
			<?php settings_errors( self::OPTION ); ?>
			<?php if ( 'ok' === $test ) : ?>
				<div class="notice notice-success is-dismissible"><p><?php esc_html_e( 'AIVA received the test. You\'re connected.', 'aiva-crm-forms' ); ?></p></div>
			<?php elseif ( 'missing' === $test ) : ?>
				<div class="notice notice-warning is-dismissible"><p><?php esc_html_e( 'Add the webhook URL first.', 'aiva-crm-forms' ); ?></p></div>
			<?php elseif ( '' !== $test ) : ?>
				<div class="notice notice-error is-dismissible"><p>
					<?php
					/* translators: %s: error code such as http_401 */
					echo esc_html( sprintf( __( 'AIVA didn\'t accept the test (%s). Check the webhook URL and secret, and that WordPress Forms is switched on in AIVA.', 'aiva-crm-forms' ), $test ) );
					?>
				</p></div>
			<?php endif; ?>

			<form method="post" action="options.php">
				<?php settings_fields( 'aiva_crm_forms' ); ?>
				<table class="form-table" role="presentation">
					<tr>
						<th scope="row"><label for="aiva-crm-url"><?php esc_html_e( 'Webhook URL', 'aiva-crm-forms' ); ?></label></th>
						<td>
							<input type="url" id="aiva-crm-url" class="large-text code" name="<?php echo esc_attr( self::OPTION ); ?>[webhook_url]" value="<?php echo esc_attr( $options['webhook_url'] ); ?>" />
							<p class="description"><?php esc_html_e( 'From AIVA: Integrations → WordPress Forms.', 'aiva-crm-forms' ); ?></p>
						</td>
					</tr>
					<tr>
						<th scope="row"><label for="aiva-crm-secret"><?php esc_html_e( 'Signing secret', 'aiva-crm-forms' ); ?></label></th>
						<td>
							<input type="password" id="aiva-crm-secret" class="regular-text code" autocomplete="new-password" name="<?php echo esc_attr( self::OPTION ); ?>[secret]" value="" placeholder="<?php echo esc_attr( $options['secret'] ? '••••••••••••' : '' ); ?>" />
							<p class="description"><?php esc_html_e( 'Saved. Leave blank to keep it; paste a new one after rotating it in AIVA.', 'aiva-crm-forms' ); ?></p>
						</td>
					</tr>
					<tr>
						<th scope="row"><?php esc_html_e( 'Send submissions from', 'aiva-crm-forms' ); ?></th>
						<td>
							<fieldset>
								<?php foreach ( self::sources() as $key => $source ) : ?>
									<label style="display:block;margin-bottom:6px">
										<input type="checkbox" name="<?php echo esc_attr( self::OPTION ); ?>[enabled][<?php echo esc_attr( $key ); ?>]" value="1" <?php checked( ! empty( $options['enabled'][ $key ] ) ); ?> />
										<?php echo esc_html( $source[0] ); ?>
										<?php if ( ! $source[1] ) : ?>
											<span class="description">(<?php esc_html_e( 'not installed', 'aiva-crm-forms' ); ?>)</span>
										<?php endif; ?>
									</label>
								<?php endforeach; ?>
							</fieldset>
						</td>
					</tr>
				</table>
				<?php submit_button(); ?>
			</form>

			<h2><?php esc_html_e( 'Check the connection', 'aiva-crm-forms' ); ?></h2>
			<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
				<input type="hidden" name="action" value="aiva_crm_forms_test" />
				<?php wp_nonce_field( 'aiva_crm_forms_test' ); ?>
				<?php submit_button( __( 'Send a test to AIVA', 'aiva-crm-forms' ), 'secondary', 'submit', false ); ?>
			</form>
		</div>
		<?php
	}

	public static function send_test() {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'You are not allowed to do that.', 'aiva-crm-forms' ), 403 );
		}
		check_admin_referer( 'aiva_crm_forms_test' );
		$options = self::options();
		$result  = 'missing';
		if ( ! empty( $options['webhook_url'] ) ) {
			$response = self::post(
				$options,
				array(
					'aiva_test' => true,
					'plugin'    => 'aiva',
					'site_url'  => home_url( '/' ),
					'fields'    => array(),
				),
				true
			);
			if ( is_wp_error( $response ) ) {
				$result = 'unreachable';
			} else {
				$code   = (int) wp_remote_retrieve_response_code( $response );
				$result = ( $code >= 200 && $code < 300 ) ? 'ok' : 'http_' . $code;
			}
		}
		wp_safe_redirect( add_query_arg( array( 'page' => 'aiva-crm-forms', 'aiva_test' => $result ), admin_url( 'options-general.php' ) ) );
		exit;
	}

	// ------------------------------------------------------------------ sending

	private static function text( $value ) {
		if ( is_array( $value ) ) {
			$parts = array();
			array_walk_recursive(
				$value,
				function ( $item ) use ( &$parts ) {
					if ( is_scalar( $item ) && '' !== trim( (string) $item ) ) {
						$parts[] = (string) $item;
					}
				}
			);
			$value = implode( ', ', $parts );
		} elseif ( is_bool( $value ) ) {
			$value = $value ? 'Yes' : 'No';
		} elseif ( ! is_scalar( $value ) ) {
			$value = '';
		}
		$value = sanitize_textarea_field( (string) $value );
		return function_exists( 'mb_substr' ) ? mb_substr( $value, 0, self::MAX_LEN ) : substr( $value, 0, self::MAX_LEN );
	}

	private static function page_url() {
		$referer = wp_get_referer();
		return $referer ? esc_url_raw( $referer ) : '';
	}

	private static function utm( $page_url ) {
		$query = $page_url ? wp_parse_url( $page_url, PHP_URL_QUERY ) : '';
		if ( ! $query ) {
			return array();
		}
		wp_parse_str( $query, $params );
		$utm = array();
		foreach ( array( 'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'gclid', 'fbclid' ) as $key ) {
			if ( ! empty( $params[ $key ] ) && is_scalar( $params[ $key ] ) ) {
				$utm[ $key ] = sanitize_text_field( (string) $params[ $key ] );
			}
		}
		return $utm ? array( 'utm' => $utm ) : array();
	}

	private static function send( $source, $form_id, $form_name, $fields, $entry_id = '' ) {
		$options = self::options();
		if ( empty( $options['webhook_url'] ) || empty( $options['enabled'][ $source ] ) ) {
			return;
		}
		$clean = array();
		foreach ( (array) $fields as $field ) {
			$type = isset( $field['type'] ) ? strtolower( (string) $field['type'] ) : '';
			if ( in_array( $type, self::$skip_types, true ) || ! isset( $field['key'] ) || '' === (string) $field['key'] ) {
				continue;
			}
			$value = self::text( isset( $field['value'] ) ? $field['value'] : '' );
			if ( '' === $value ) {
				continue;
			}
			$clean[] = array(
				'key'   => substr( (string) $field['key'], 0, 190 ),
				'label' => sanitize_text_field( isset( $field['label'] ) && '' !== (string) $field['label'] ? (string) $field['label'] : (string) $field['key'] ),
				'type'  => $type,
				'value' => $value,
			);
		}
		if ( empty( $clean ) ) {
			return;
		}
		$page    = self::page_url();
		$payload = array(
			'plugin'       => $source,
			'form_id'      => (string) $form_id,
			'form_name'    => sanitize_text_field( (string) $form_name ),
			'entry_id'     => (string) $entry_id,
			'fields'       => $clean,
			'page_url'     => $page,
			'site_url'     => home_url( '/' ),
			'submitted_at' => gmdate( 'c' ),
		) + self::utm( $page );
		self::post( $options, $payload, false );
	}

	private static function post( $options, $payload, $blocking ) {
		$body = wp_json_encode( $payload );
		if ( false === $body ) {
			return new WP_Error( 'aiva_json', 'Could not encode the submission.' );
		}
		$headers = array(
			'Content-Type' => 'application/json',
			'User-Agent'   => 'AIVA-WordPress/' . self::VERSION . '; ' . home_url( '/' ),
		);
		if ( ! empty( $options['secret'] ) ) {
			$timestamp                   = time();
			$headers['X-AIVA-Signature'] = 't=' . $timestamp . ',v1=' . hash_hmac( 'sha256', $timestamp . '.' . $body, $options['secret'] );
		}
		return wp_remote_post(
			$options['webhook_url'],
			array(
				'timeout'     => $blocking ? 15 : 5,
				'blocking'    => (bool) $blocking,
				'headers'     => $headers,
				'body'        => $body,
				'data_format' => 'body',
				'redirection' => 0,
			)
		);
	}

	private static function is_assoc( $value ) {
		return is_array( $value ) && array_keys( $value ) !== range( 0, count( $value ) - 1 );
	}

	// ------------------------------------------------------------------ form plugins

	public static function contact_form_7( $contact_form ) {
		if ( ! class_exists( 'WPCF7_Submission' ) || ! is_object( $contact_form ) ) {
			return;
		}
		$submission = WPCF7_Submission::get_instance();
		if ( ! $submission ) {
			return;
		}
		$types = array();
		if ( method_exists( $contact_form, 'scan_form_tags' ) ) {
			foreach ( $contact_form->scan_form_tags() as $tag ) {
				if ( ! empty( $tag->name ) ) {
					$types[ $tag->name ] = $tag->basetype;
				}
			}
		}
		$fields = array();
		foreach ( (array) $submission->get_posted_data() as $name => $value ) {
			$name = (string) $name;
			if ( '' === $name || 0 === strpos( $name, '_' ) ) {
				continue;
			}
			$fields[] = array( 'key' => $name, 'label' => $name, 'type' => isset( $types[ $name ] ) ? $types[ $name ] : '', 'value' => $value );
		}
		self::send( 'contact-form-7', $contact_form->id(), $contact_form->title(), $fields );
	}

	public static function wpforms( $fields, $entry, $form_data, $entry_id ) {
		$out = array();
		foreach ( (array) $fields as $id => $field ) {
			$label = isset( $field['name'] ) ? (string) $field['name'] : 'Field ' . $id;
			$type  = isset( $field['type'] ) ? (string) $field['type'] : '';
			if ( 'name' === $type && ( ! empty( $field['first'] ) || ! empty( $field['last'] ) ) ) {
				$out[] = array( 'key' => 'field_' . $id . '_first', 'label' => 'First name', 'type' => 'text', 'value' => isset( $field['first'] ) ? $field['first'] : '' );
				$out[] = array( 'key' => 'field_' . $id . '_last', 'label' => 'Last name', 'type' => 'text', 'value' => isset( $field['last'] ) ? $field['last'] : '' );
				continue;
			}
			$out[] = array( 'key' => 'field_' . $id, 'label' => $label, 'type' => $type, 'value' => isset( $field['value'] ) ? $field['value'] : '' );
		}
		$title = isset( $form_data['settings']['form_title'] ) ? $form_data['settings']['form_title'] : '';
		self::send( 'wpforms', isset( $form_data['id'] ) ? $form_data['id'] : '', $title, $out, $entry_id );
	}

	public static function gravity_forms( $entry, $form ) {
		if ( ! function_exists( 'rgar' ) || empty( $form['fields'] ) ) {
			return;
		}
		$out = array();
		foreach ( $form['fields'] as $field ) {
			$label  = ! empty( $field->adminLabel ) ? $field->adminLabel : $field->label;
			$inputs = method_exists( $field, 'get_entry_inputs' ) ? $field->get_entry_inputs() : null;
			if ( is_array( $inputs ) ) {
				foreach ( $inputs as $input ) {
					$value = rgar( $entry, (string) $input['id'] );
					if ( '' === $value || null === $value ) {
						continue;
					}
					$sub   = isset( $input['label'] ) ? $input['label'] : '';
					$out[] = array( 'key' => 'input_' . str_replace( '.', '_', (string) $input['id'] ), 'label' => trim( $sub . ' (' . $label . ')' ), 'type' => $field->type, 'value' => $value );
				}
				continue;
			}
			$value = method_exists( $field, 'get_value_export' ) ? $field->get_value_export( $entry, (string) $field->id, true ) : rgar( $entry, (string) $field->id );
			$out[] = array( 'key' => 'input_' . $field->id, 'label' => $label, 'type' => $field->type, 'value' => $value );
		}
		self::send( 'gravity-forms', rgar( $form, 'id' ), rgar( $form, 'title' ), $out, rgar( $entry, 'id' ) );
	}

	public static function elementor( $record, $handler ) {
		if ( ! is_object( $record ) || ! method_exists( $record, 'get' ) ) {
			return;
		}
		$out = array();
		foreach ( (array) $record->get( 'fields' ) as $id => $field ) {
			$out[] = array(
				'key'   => (string) $id,
				'label' => ! empty( $field['title'] ) ? $field['title'] : (string) $id,
				'type'  => isset( $field['type'] ) ? $field['type'] : '',
				'value' => isset( $field['value'] ) ? $field['value'] : '',
			);
		}
		self::send( 'elementor', $record->get_form_settings( 'id' ), $record->get_form_settings( 'form_name' ), $out );
	}

	public static function ninja_forms( $form_data ) {
		if ( empty( $form_data['fields'] ) || ! is_array( $form_data['fields'] ) ) {
			return;
		}
		$out = array();
		foreach ( $form_data['fields'] as $field ) {
			$key   = ! empty( $field['key'] ) ? $field['key'] : ( isset( $field['id'] ) ? 'field_' . $field['id'] : '' );
			$out[] = array(
				'key'   => $key,
				'label' => isset( $field['label'] ) ? $field['label'] : $key,
				'type'  => isset( $field['type'] ) ? $field['type'] : '',
				'value' => isset( $field['value'] ) ? $field['value'] : '',
			);
		}
		$title    = isset( $form_data['settings']['title'] ) ? $form_data['settings']['title'] : '';
		$entry_id = isset( $form_data['actions']['save']['sub_id'] ) ? $form_data['actions']['save']['sub_id'] : '';
		self::send( 'ninja-forms', isset( $form_data['form_id'] ) ? $form_data['form_id'] : '', $title, $out, $entry_id );
	}

	public static function fluent_forms( $entry_id, $form_data, $form ) {
		// Newer Fluent Forms fires both the old and the new hook; send once.
		static $sent = array();
		if ( isset( $sent[ $entry_id ] ) ) {
			return;
		}
		$sent[ $entry_id ] = true;
		$skip              = array( 'g-recaptcha-response', 'h-captcha-response', 'cf-turnstile-response' );
		$out               = array();
		foreach ( (array) $form_data as $name => $value ) {
			$name = (string) $name;
			if ( '' === $name || 0 === strpos( $name, '_' ) || in_array( $name, $skip, true ) ) {
				continue;
			}
			if ( self::is_assoc( $value ) ) {
				foreach ( $value as $sub => $sub_value ) {
					if ( is_scalar( $sub_value ) ) {
						$out[] = array( 'key' => $name . '.' . $sub, 'label' => ucwords( str_replace( array( '_', '-' ), ' ', (string) $sub ) ), 'type' => '', 'value' => $sub_value );
					}
				}
				continue;
			}
			$out[] = array( 'key' => $name, 'label' => ucwords( str_replace( array( '_', '-' ), ' ', $name ) ), 'type' => '', 'value' => $value );
		}
		self::send( 'fluent-forms', is_object( $form ) && isset( $form->id ) ? $form->id : '', is_object( $form ) && isset( $form->title ) ? $form->title : '', $out, $entry_id );
	}

	public static function formidable( $entry_id, $form_id ) {
		if ( ! class_exists( 'FrmEntry' ) || ! class_exists( 'FrmField' ) ) {
			return;
		}
		$entry = FrmEntry::getOne( $entry_id, true );
		if ( ! $entry || empty( $entry->metas ) ) {
			return;
		}
		$out = array();
		foreach ( (array) FrmField::get_all_for_form( $form_id ) as $field ) {
			if ( ! isset( $entry->metas[ $field->id ] ) ) {
				continue;
			}
			$out[] = array(
				'key'   => ! empty( $field->field_key ) ? $field->field_key : 'field_' . $field->id,
				'label' => $field->name,
				'type'  => $field->type,
				'value' => $entry->metas[ $field->id ],
			);
		}
		$form = class_exists( 'FrmForm' ) ? FrmForm::getOne( $form_id ) : null;
		self::send( 'formidable', $form_id, $form ? $form->name : '', $out, $entry_id );
	}
}

register_activation_hook( __FILE__, array( 'AIVA_CRM_Forms', 'activate' ) );
add_action( 'plugins_loaded', array( 'AIVA_CRM_Forms', 'init' ) );
"""

UNINSTALL_PHP = r"""<?php
/**
 * Removes the plugin's settings when it is deleted.
 */
if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}
delete_option( 'aiva_crm_forms' );
"""

README_TXT = r"""=== AIVA CRM Forms ===
Tags: crm, leads, contact form 7, wpforms, gravity forms
Requires at least: 5.8
Tested up to: 6.6
Requires PHP: 7.4
Stable tag: {{VERSION}}
License: GPLv2 or later

Sends form submissions to AIVA CRM, where they become leads.

== Description ==

Works with Contact Form 7, WPForms, Gravity Forms, Elementor Pro forms, Ninja Forms,
Fluent Forms and Formidable Forms. Every submission is sent to your AIVA workspace in
the background; your forms, emails and confirmations keep working exactly as before.

* This copy is already set up for your workspace: activate it and you're done.
* Password, payment, captcha and file-upload fields are never sent.
* Each request is signed (X-AIVA-Signature, HMAC-SHA256) so AIVA can check it came from your site.
* Settings → AIVA CRM lets you change the address or secret, choose which form plugins to send from, and send a test.

In AIVA, map form fields to lead fields, pick the lead owner and source per form, and
choose whether repeat submissions update the existing lead (Integrations → WordPress Forms).

== Installation ==

1. In WordPress, go to Plugins → Add New → Upload Plugin and upload this zip.
2. Activate "AIVA CRM Forms".
3. Optional: Settings → AIVA CRM → "Send a test to AIVA".

The file aiva-config.php holds the address and secret this copy was downloaded with.
It's only read when the plugin is activated and can't be opened from the web.

== Changelog ==

= {{VERSION}} =
* First release.
"""
