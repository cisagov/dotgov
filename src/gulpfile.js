/* gulpfile.js */

const gulp = require('gulp');
const webpack = require('webpack-stream');
const uswds = require('@uswds/compile');

const ASSETS_DIR = './registrar/assets/';
const JS_MODULES_SRC = ASSETS_DIR + 'modules/*.js';
const JS_BUNDLE_DEST = ASSETS_DIR + 'js';

/**
 * USWDS version
 * Set the version of USWDS you're using (2 or 3)
 */
uswds.settings.version = 3;

/**
 * Path settings
 * Set as many as you need
 */
uswds.paths.dist.css = ASSETS_DIR + 'css';
uswds.paths.dist.sass = ASSETS_DIR + 'sass';
uswds.paths.dist.theme = ASSETS_DIR + 'sass/_theme';
uswds.paths.dist.fonts = ASSETS_DIR + 'fonts';
uswds.paths.dist.js = ASSETS_DIR + 'js';
uswds.paths.dist.img = ASSETS_DIR + 'img';

/**
 * Task: Bundle JavaScript modules using Webpack
 */
gulp.task('bundle-js', () => {
    return gulp
        .src(JS_MODULES_SRC)
        .pipe(
            webpack({
                mode: 'production', // Use 'development' for debugging
                output: {
                    filename: 'get-gov.js',
                },
                module: {
                    rules: [
                        {
                            test: /\.js$/,
                            exclude: /node_modules/,
                            use: {
                                loader: 'babel-loader',
                                options: {
                                    presets: ['@babel/preset-env'],
                                },
                            },
                        },
                    ],
                },
            })
        )
        .pipe(gulp.dest(JS_BUNDLE_DEST));
});

/**
 * Task: Watch for changes in JavaScript modules
 */
gulp.task('watch-js', () => {
    gulp.watch(JS_MODULES_SRC, gulp.series('bundle-js'));
});

/**
 * Combine all watch tasks
 * Using gulp outside of uswds compile's dependencies seems to leverage a different sass compiler
 * The more up-to-date compiler triggers mixed declarations deprecation warnings
 * We expect this to be resolved in a future uswds release on the code side: https://github.com/uswds/uswds/issues/5980
 * USWDS internally uses its version of Dart Sass (sass), and the USWDS compilation process expects a specific Sass behavior.
 * Babel/Webpack introduces loaders (sass-loader or similar) that interact with Sass. If a newer version of Sass is used due to dependency resolution, the new Sass behavior applies, leading to the warnings.
 */
gulp.task('watch', gulp.parallel('watch-js', uswds.watch));

/**
 * Exports
 * Add as many as you need
 * Some tasks combine USWDS compilation and JavaScript precompilation.
 */
exports.default = gulp.series(uswds.compile, 'bundle-js');
exports.init = uswds.init;
exports.compile = gulp.series(uswds.compile, 'bundle-js');
exports.watch = gulp.parallel('watch');
exports.copyAssets = uswds.copyAssets
exports.updateUswds = uswds.updateUswds
